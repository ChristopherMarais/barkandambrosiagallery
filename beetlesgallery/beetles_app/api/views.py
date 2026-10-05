from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from ..areas import ANNOTATE, BOXES, has_area
from django.http import FileResponse
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from beetlesgallery.beetles_app.models import ImageAsset, Beetles, ImageLock
from .. import roi_defaults
from .serializers import ImageAssetSerializer, BeetlesSerializer, SpeciesSerializer
import json
import zipfile
import os
import logging
from io import BytesIO
from datetime import datetime

logger = logging.getLogger(__name__)


class _RollBack(Exception):
    """Raised inside a transaction.atomic() block to undo it without an error to report."""


class IsStaffUser(IsAuthenticated):
    """
    Permission class for the annotation API: the user may at least edit boxes (areas.BOXES; "edit names and
    records" includes it). Everything beyond boxes also needs areas.ANNOTATE, checked with require_records().
    """
    def has_permission(self, request, view):
        is_authenticated = super().has_permission(request, view)
        return bool(is_authenticated and has_area(request.user, BOXES))


# What someone who may only edit boxes can send for an ROI
BOX_FIELDS = {"bbox_x", "bbox_y", "bbox_width", "bbox_height"}
BOX_ONLY_ALLOWED = BOX_FIELDS | {"id", "image_asset", "image_asset_id"}


def require_records(request):
    """Names, metadata, validation and deleting records need "edit names and records" (areas.ANNOTATE)."""
    if not has_area(request.user, ANNOTATE):
        raise PermissionDenied("Your account can edit boxes only.")


def require_box_fields_only(request, data):
    """Someone who may only edit boxes can change nothing but the box (and say which image a new box is on)."""
    if has_area(request.user, ANNOTATE):
        return
    # (removing the last box sends bbox_is_validated false with it: that is part of removing a box)
    extra = sorted(k for k, v in (data or {}).items()
                   if k not in BOX_ONLY_ALLOWED and not (k == "bbox_is_validated" and v is False))
    if extra:
        raise PermissionDenied(f"Your account can edit boxes only (not {', '.join(extra)}).")


class ImageAssetViewSet(viewsets.ModelViewSet):
    """
    API endpoint for ImageAsset records.
    """
    # --> UPDATED: Filter out soft-deleted images
    queryset = ImageAsset.objects.filter(is_deleted=False)
    serializer_class = ImageAssetSerializer
    permission_classes = [IsStaffUser] # Required so only staff can validate/update

    # --> NEW: Soft delete attribution
    def perform_destroy(self, instance):
        require_records(self.request)
        instance.delete(deleted_by=self.request.user)

    def perform_update(self, serializer):
        require_records(self.request)
        # Automatically track who updated/validated the image
        serializer.save(last_updated_by=self.request.user)

    def perform_create(self, serializer):
        require_records(self.request)
        serializer.save()

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        asset = self.get_object()
        if not asset.image_file:
            return Response({'error': 'No image file available'}, status=404)
        return FileResponse(asset.image_file.open('rb'))

    @action(detail=True, methods=['post'], url_path='classify')
    def classify(self, request, pk=None):
        """
        "Classify with AI": add the classifier's boxes and species to this image as new, unvalidated ROIs.
        POST /api/v1/image-assets/{uuid}/classify/  {"architecture": "ibbi_dinov3", "box_threshold": 0.25}
        (the model keys are in beetlesgallery/tools/ibbi_models.py; the names from before ibbi 0.3 still work)
        """
        from beetlesgallery.tools import ibbi_models

        from ..classify_assist import ClassifyError, add_rois, call_classifier
        require_records(request)   # it adds species names too
        asset = self.get_object()
        if not asset.image_file:
            return Response({'error': 'This image has no file.'}, status=400)
        lock = getattr(asset, 'active_lock', None)
        if lock and lock.locked_by != request.user and not lock.is_expired():
            return Response({'error': f'Being edited by {lock.locked_by.username}'}, status=status.HTTP_409_CONFLICT)
        try:
            threshold = min(1.0, max(0.05, float(request.data.get('box_threshold', 0.25))))
        except (TypeError, ValueError):
            threshold = 0.25
        try:
            with asset.image_file.open('rb') as fh:
                data = fh.read()
            result = call_classifier(data, os.path.basename(asset.image_file.name), 'image/jpeg',
                                     request.data.get('architecture') or ibbi_models.DEFAULT, threshold)
            created, skipped = add_rois(asset, result, request.user)
        except ClassifyError as exc:
            return Response({'error': str(exc)}, status=502)
        return Response({'added': len(created), 'already_boxed': skipped, 'model': result.get('model_used', '')})

    @action(detail=True, methods=['post'], url_path='heartbeat')
    def heartbeat(self, request, pk=None):
        """
        Refresh the lock on this image to prevent concurrent editing.
        Called automatically by the frontend every 60 seconds.
        """
        asset = self.get_object()
        
        if hasattr(asset, 'active_lock') and asset.active_lock:
            lock = asset.active_lock
            if lock.locked_by != request.user and not lock.is_expired():
                return Response(
                    {'error': f'Being edited by {lock.locked_by.username}'}, 
                    status=status.HTTP_409_CONFLICT
                )
            if lock.locked_by == request.user:
                lock.updated_at = timezone.now()
                lock.save(update_fields=['updated_at'])
                return Response({'status': 'Lock refreshed'})
                
        ImageLock.objects.update_or_create(
            image_asset=asset,
            defaults={'locked_by': request.user, 'updated_at': timezone.now()}
        )
        return Response({'status': 'Lock acquired'})

    @action(detail=True, methods=['post'], permission_classes=[IsStaffUser])
    def lock(self, request, pk=None):
        """
        Acquire a lock on this image for the current user.
        Prevents concurrent editing by multiple users.

        POST /api/v1/image-assets/{uuid}/lock/

        Returns:
            - 200: Lock acquired successfully
            - 409: Image already locked by another user (with lock details)
        """
        asset = self.get_object()
        user = request.user

        ImageLock.cleanup_expired_locks()

        try:
            existing_lock = ImageLock.objects.select_related('locked_by').get(image_asset=asset)
            if existing_lock.locked_by == user:
                existing_lock.save()
                return Response({
                    'success': True,
                    'message': 'Lock refreshed',
                    'locked_by': user.username,
                    'locked_at': existing_lock.locked_at
                })

            if existing_lock.is_expired():
                existing_lock.delete()
            else:
                return Response({
                    'success': False,
                    'error': 'Image is currently locked by another user',
                    'locked_by': existing_lock.locked_by.username,
                    'locked_at': existing_lock.locked_at,
                    'locked_for_minutes': ImageLock.LOCK_TIMEOUT_MINUTES
                }, status=409)

        except ImageLock.DoesNotExist:
            pass

        lock = ImageLock.objects.create(
            image_asset=asset,
            locked_by=user
        )

        return Response({
            'success': True,
            'message': 'Lock acquired',
            'locked_by': user.username,
            'locked_at': lock.locked_at
        })

    @action(detail=True, methods=['post'], permission_classes=[IsStaffUser])
    def unlock(self, request, pk=None):
        """
        Release the lock on this image for the current user.

        POST /api/v1/image-assets/{uuid}/unlock/

        Returns:
            - 200: Lock released successfully
            - 404: No lock exists or not locked by current user
        """
        asset = self.get_object()
        user = request.user

        try:
            lock = ImageLock.objects.get(image_asset=asset, locked_by=user)
            lock.delete()
            return Response({
                'success': True,
                'message': 'Lock released'
            })
        except ImageLock.DoesNotExist:
            return Response({
                'success': False,
                'message': 'No active lock found for this user'
            }, status=404)

    @action(detail=True, methods=['post'], permission_classes=[IsStaffUser], url_path='unvalidate')
    def unvalidate(self, request, pk=None):
        """
        Staff endpoint to unvalidate an image and all its ROIs.
        POST /api/v1/image-assets/{uuid}/unvalidate/
        """
        require_records(request)
        asset = self.get_object()
        asset.unvalidate(user=request.user)
        return Response({
            'success': True,
            'is_validated': False,
            'message': 'Image and all associated ROIs have been marked as unvalidated.'
        })

    @action(detail=True, methods=['post'], permission_classes=[IsStaffUser], url_path='validate')
    def validate(self, request, pk=None):
        """
        Staff endpoint to validate an image and all its ROIs.
        POST /api/v1/image-assets/{uuid}/validate/
        """
        require_records(request)
        asset = self.get_object()
        is_validated = asset.validate(user=request.user)
        return Response({
            'success': True,
            'is_validated': is_validated,
            'message': ('Image and all associated ROIs have been marked as validated.' if is_validated
                        else 'Image has no bounding boxes, so it cannot be validated.')
        })


class BeetlesViewSet(viewsets.ModelViewSet):
    serializer_class = BeetlesSerializer
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        queryset = Beetles.objects.select_related(
            'image_asset',
            'taxon',
            'bbox_created_by',
            'bbox_validated_by'
        ).filter(is_deleted=False)

        subfamily = self.request.query_params.get('subfamily')
        if subfamily:
            queryset = queryset.filter(taxon__subfamily__iexact=subfamily)

        image_asset_id = self.request.query_params.get('image_asset')
        if image_asset_id:
            queryset = queryset.filter(image_asset_id=image_asset_id)

        has_bbox = self.request.query_params.get('has_bbox')
        if has_bbox == 'true':
            queryset = queryset.exclude(bbox_x__isnull=True)
        elif has_bbox == 'false':
            queryset = queryset.filter(bbox_x__isnull=True)

        return queryset

    def perform_destroy(self, instance):
        # Someone who edits boxes only may remove a box that is nothing more: no name, not validated.
        if not has_area(self.request.user, ANNOTATE) and (instance.depicts_valid_name_id or instance.bbox_is_validated):
            raise PermissionDenied("Your account can edit boxes only: this ROI has a name or is validated.")
        instance.delete(deleted_by=self.request.user)

    def perform_create(self, serializer):
        """
        Create a new bbox annotation or update an existing template beetle.
        Delegates all database writes strictly to serializer.save().
        """
        require_box_fields_only(self.request, serializer.initial_data)
        # Base audit fields for any creation or update
        save_kwargs = {
            'last_updated_by': self.request.user
        }

        # Determine if the payload contains bounding box geometry
        if serializer.validated_data.get('bbox_x') is not None:
            save_kwargs.update({
                'bbox_created_by': self.request.user,
                'bbox_created_at': timezone.now()
            })

            # Extract the resolved ImageAsset object injected by BeetlesSerializer.validate()
            image_asset = serializer.validated_data.get('image_asset')

            if image_asset:
                # 1. A "Template/Ghost" ROI (has metadata, but NULL bbox coordinates) is filled in;
                #    serializer.instance makes serializer.save() an UPDATE instead of an INSERT.
                # 2. Otherwise the new ROI copies the metadata of the most recent existing ROI.
                #    (The same rules give the classifier's boxes their metadata, see roi_defaults.py.)
                template_beetle = roi_defaults.template_roi(image_asset)
                if template_beetle:
                    serializer.instance = template_beetle
                else:
                    save_kwargs.update(roi_defaults.inherited_fields(roi_defaults.latest_roi(image_asset)))

        # 3. Terminal Execution: Execute the save exclusively through the serializer pipeline
        serializer.save(**save_kwargs)

    def perform_update(self, serializer):
        require_box_fields_only(self.request, serializer.initial_data)
        serializer.instance._name_by_hand = True   # a curator's name always shows (identification.py)
        # 3. If frontend sends a PATCH setting bbox to null (Last ROI Deletion)
        if 'bbox_x' in serializer.validated_data and serializer.validated_data.get('bbox_x') is None:
            # We DO NOT delete the record. We keep the Ghost ROI alive.
            serializer.save(
                bbox_created_by=None,
                bbox_created_at=None,
                bbox_validated_by=None,
                bbox_validated_at=None,
                bbox_is_validated=False,
                last_updated_by=self.request.user
            )
            return

        if serializer.validated_data.get('bbox_is_validated') is True:
            serializer.save(
                bbox_validated_by=self.request.user,
                bbox_validated_at=timezone.now(),
                last_updated_by=self.request.user
            )
        elif serializer.validated_data.get('bbox_is_validated') is False:
            serializer.save(
                bbox_validated_by=None,
                bbox_validated_at=None,
                bbox_is_validated=False,
                last_updated_by=self.request.user
            )
        else:
            serializer.save(last_updated_by=self.request.user)

    @action(detail=True, methods=['post'], permission_classes=[IsStaffUser], url_path='unvalidate')
    def unvalidate(self, request, pk=None):
        """
        Staff endpoint to unvalidate a specific ROI.
        POST /api/v1/beetles/{uuid}/unvalidate/
        """
        require_records(request)
        beetle = self.get_object()
        beetle.unvalidate(user=request.user)
        return Response({
            'success': True,
            'bbox_is_validated': False,
            'image_is_validated': beetle.image_asset.is_validated if beetle.image_asset else False,
            'message': 'ROI unvalidated successfully.'
        })

    @action(detail=True, methods=['post'], permission_classes=[IsStaffUser], url_path='validate')
    def validate(self, request, pk=None):
        """
        Staff endpoint to validate a specific ROI.
        POST /api/v1/beetles/{uuid}/validate/
        """
        require_records(request)
        beetle = self.get_object()
        beetle.validate(user=request.user)
        return Response({
            'success': True,
            'bbox_is_validated': True,
            'image_is_validated': beetle.image_asset.is_validated if beetle.image_asset else False,
            'message': 'ROI validated successfully.'
        })

    @action(detail=False, methods=['patch'], url_path='bulk-update')
    def bulk_update(self, request):
        """
        Accepts a JSON array of dicts: [{"id": "uuid", "bbox_x": 0.5, ...}, ...]
        Updates all records in a single atomic database transaction.
        """
        data = request.data
        if not isinstance(data, list):
            return Response({"error": "Expected a list of objects."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Extract IDs and map existing instances from the database
        ids = [item.get('id') for item in data if item.get('id')]
        instances = Beetles.objects.filter(id__in=ids, is_deleted=False)
        instance_map = {str(inst.id): inst for inst in instances}

        results = []
        invalid = None
        try:
            # 2. Open a single database transaction
            with transaction.atomic():
                for item in data:
                    inst = instance_map.get(str(item.get('id')))
                    if inst:
                        # 3. Initialize serializer with partial=True for sparse updates
                        serializer = self.get_serializer(inst, data=item, partial=True)
                        if not serializer.is_valid():
                            # Field-level messages from the serializer are meant for the user.
                            invalid = {"id": str(inst.id), "errors": serializer.errors}
                            raise _RollBack()

                        # 4. Route through perform_update to trigger your existing Validation/Ghost ROI logic!
                        self.perform_update(serializer)

                        results.append(serializer.data)

            return Response(results, status=status.HTTP_200_OK)
        except _RollBack:
            # One row failed validation: the whole batch is rolled back.
            return Response(
                {"error": "Invalid data; nothing was saved.", "details": invalid},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PermissionDenied as exc:
            return Response({"error": f"{exc.detail} Nothing was saved."}, status=status.HTTP_403_FORBIDDEN)
        except Exception:
            # Anything else is a server-side problem: log it, don't echo it to the client.
            logger.exception("Bulk update failed in BeetlesViewSet.bulk_update")
            return Response(
                {"error": "Bulk update failed; nothing was saved."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=['get'], url_path='images-with-annotations')
    def images_with_annotations(self, request):
        """
        Unified API Feed for the Annotation Tool.
        """
        from django.db.models import Count, Q, Exists, OuterRef, Prefetch
        from django.core.paginator import Paginator
        from beetlesgallery.beetles_app.utils import build_query_q, filter_beetles_queryset, FILTERS_CONFIG

        page_num = int(request.GET.get('page', 1))
        page_size = min(int(request.GET.get('page_size', 50)), 200)
        ordering = request.GET.get('ordering', 'newest')
        search = request.GET.get('search', '').strip()

        beetles_qs = Beetles.objects.filter(is_deleted=False)

        if search:
            q_obj, _ = build_query_q(search)
            q_obj |= Q(image_asset__image_file__icontains=search) | Q(image_asset__id__icontains=search)
            beetles_qs = beetles_qs.filter(q_obj)

        active_filters = {}
        for cfg in FILTERS_CONFIG:
            vals = request.GET.getlist(cfg["param"])
            clean_vals = [v.strip() for v in vals if v.strip()]
            if clean_vals:
                active_filters[cfg["param"]] = clean_vals

        if active_filters:
            beetles_qs = filter_beetles_queryset(beetles_qs, active_filters, None, None, None, None)

        # Enforce is_deleted=False across the board
        valid_image_ids = beetles_qs.filter(is_deleted=False).values('image_asset_id')

        image_qs = ImageAsset.objects.filter(
            id__in=valid_image_ids, is_deleted=False
        ).select_related('active_lock__locked_by').prefetch_related(
            Prefetch('specimens', queryset=Beetles.objects.filter(is_deleted=False).only('id', 'image_asset_id'))
        )

        unvalidated_rois = Beetles.objects.filter(
            image_asset_id=OuterRef('pk'),
            bbox_x__isnull=False,
            bbox_is_validated=False,
            is_deleted=False
        )

        image_qs = image_qs.annotate(
            roi_count=Count('specimens', filter=Q(specimens__bbox_x__isnull=False, specimens__is_deleted=False), distinct=True),
            has_unvalidated_boxes=Exists(unvalidated_rois)
        )

        # Game label proposals waiting for a curator (game_queue): filter to them, or sort them by confidence.
        from beetlesgallery.beetles_app import game_queue
        game_filter = request.GET.get('game', '')
        if ordering == 'game_confidence' and game_filter not in ('any', 'expert'):
            game_filter = 'any'
        game_ranked = None
        queue = game_queue.by_image()
        if game_filter in ('any', 'expert'):
            game_ranked = game_queue.ranked_ids(queue, expert_only=game_filter == 'expert')
            image_qs = image_qs.filter(id__in=game_ranked)
        elif game_filter == 'reported':
            # photos players flagged from the game, waiting for a curator (they are out of the game until then)
            from beetlesgallery.beetles_app.models import GameReport
            image_qs = image_qs.filter(id__in=GameReport.objects.filter(status=GameReport.Status.OPEN)
                                       .values('roi__image_asset_id'))
        elif game_filter == 'applied':
            # labels the game wrote into the database (curator-accepted or automatic), to check or revert (#427)
            from beetlesgallery.beetles_app.game_applied import applied_rois
            image_qs = image_qs.filter(id__in=applied_rois().values('image_asset_id'))
        elif game_filter == 'disputed':
            # validated labels most reliable players dispute (game_label_check): likely mislabelled
            from beetlesgallery.beetles_app.game_label_check import LABEL_CHECK_USER
            from beetlesgallery.beetles_app.models import GameReport
            image_qs = image_qs.filter(id__in=GameReport.objects.filter(
                status=GameReport.Status.OPEN, reporter__username=LABEL_CHECK_USER).values('roi__image_asset_id'))

        # PERFORMANCE: Only compute heavy aggregate stats on initial page load (page 1)
        stats_data = None
        if page_num == 1:
            img_stats = image_qs.aggregate(
                val_count=Count('id', filter=Q(is_validated=True)),
                unval_count=Count('id', filter=Q(is_validated=False)),
                no_bbox_count=Count('id', filter=Q(roi_count=0))
            )
            roi_stats = beetles_qs.filter(bbox_x__isnull=False, is_deleted=False).aggregate(
                val_count=Count('id', filter=Q(bbox_is_validated=True)),
                unval_count=Count('id', filter=Q(bbox_is_validated=False))
            )
            stats_data = {
                'images_validated': img_stats['val_count'] or 0,
                'images_unvalidated': img_stats['unval_count'] or 0,
                'images_no_bbox': img_stats['no_bbox_count'] or 0,
                'rois_validated': roi_stats['val_count'] or 0,
                'rois_unvalidated': roi_stats['unval_count'] or 0
            }

        # Only known sorts are accepted (the value used to go straight into order_by). Empty values go last.
        from django.db.models import F
        sorts = {
            'newest': (F('created_at').desc(nulls_last=True),),
            'oldest': (F('created_at').asc(nulls_last=True),),
            'date_taken': (F('image_date_taken').desc(nulls_last=True),),
            'largest': (F('image_size_bytes').desc(nulls_last=True),),
            'resolution': (F('image_width').desc(nulls_last=True),),
            'name': (F('full_path_at_import').asc(),),
            'unvalidated_first': (F('is_validated').asc(), F('created_at').desc(nulls_last=True)),
        }
        image_qs = image_qs.order_by(*sorts.get(ordering, sorts['newest']), 'id')

        if ordering == 'game_confidence':
            present = {str(i) for i in image_qs.values_list('id', flat=True)}
            paginator = Paginator([i for i in game_ranked if i in present], page_size)
            page_obj = paginator.get_page(page_num)
            by_id = {str(img.id): img for img in image_qs.filter(id__in=list(page_obj.object_list))}
            page_obj.object_list = [by_id[i] for i in page_obj.object_list if i in by_id]
        else:
            paginator = Paginator(image_qs, page_size)
            page_obj = paginator.get_page(page_num)
        total_count = paginator.count

        ImageLock.cleanup_expired_locks()

        images = []
        for img in page_obj.object_list:
            lock_info = None
            if hasattr(img, 'active_lock') and img.active_lock:
                lock = img.active_lock
                if not lock.is_expired():
                    lock_info = {
                        'locked_by': lock.locked_by.username,
                        'locked_at': lock.locked_at.isoformat()
                    }

            specimens = list(img.specimens.all())
            first_specimen = specimens[0] if specimens else None

            images.append({
                'image_asset_id': str(img.id),
                'beetle_id': str(first_specimen.id) if first_specimen else None, 
                'filename': os.path.basename(img.image_file.name) if img.image_file else 'unknown',
                'thumbnail_url': img.thumb_small.url if img.thumb_small else None,
                'full_image_url': img.display_url,
                'annotation_count': img.roi_count,
                'has_unvalidated_boxes': img.has_unvalidated_boxes,
                'is_validated': img.is_validated,
                'created_at': img.created_at.isoformat() if img.created_at else None,
                'lock': lock_info,
                'game': game_queue.public(queue[str(img.id)]) if str(img.id) in queue else None,
            })

        base_url = request.build_absolute_uri(request.path)
        next_url = None
        prev_url = None
        
        query_string = request.GET.copy()
        if 'page' in query_string: query_string.pop('page')

        if page_obj.has_next():
            query_string['page'] = page_obj.next_page_number()
            next_url = f"{base_url}?{query_string.urlencode()}"

        if page_obj.has_previous():
            query_string['page'] = page_obj.previous_page_number()
            prev_url = f"{base_url}?{query_string.urlencode()}"

        return Response({
            'count': total_count,
            'next': next_url,
            'previous': prev_url,
            'results': images,
            'stats': stats_data  # Will be a dictionary on Page 1, and null on subsequent pages
        })


class SpeciesViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for species from the native Postgres Taxon table.
    Upgraded to ReadOnlyModelViewSet to support native DRF SearchFilter.
    """
    serializer_class = SpeciesSerializer
    filter_backends = [filters.SearchFilter]
    
    # Define the fields the frontend can search against
    search_fields = ['scientific_name', 'genus', 'species', 'subfamily', 'tribe']

    def get_queryset(self):
        from beetlesgallery.beetles_app.models import Taxon
        qs = Taxon.objects.all()

        # Retain custom exact-match parameters
        subfamily = self.request.query_params.get('subfamily')
        genus = self.request.query_params.get('genus')
        tribe = self.request.query_params.get('tribe')
        species_param = self.request.query_params.get('species')

        if subfamily:
            qs = qs.filter(subfamily__iexact=subfamily)
        if genus:
            qs = qs.filter(genus__iexact=genus)
        if tribe:
            qs = qs.filter(tribe__iexact=tribe)
        if species_param:
            qs = qs.filter(species__iexact=species_param)

        return qs
