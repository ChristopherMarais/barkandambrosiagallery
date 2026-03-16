from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.http import FileResponse
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from beetlesgallery.beetles_app.models import ImageAsset, Beetles, ImageLock
from .serializers import ImageAssetSerializer, BeetlesSerializer, SpeciesSerializer
import json
import zipfile
import os
from io import BytesIO
from datetime import datetime


class IsStaffUser(IsAuthenticated):
    """
    Permission class that checks if user is authenticated and is staff OR superuser.
    """
    def has_permission(self, request, view):
        is_authenticated = super().has_permission(request, view)
        # Authorize if the user is staff OR an administrative superuser
        return bool(is_authenticated and (request.user.is_staff or request.user.is_superuser))


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
        instance.delete(deleted_by=self.request.user)

    def perform_update(self, serializer):
        # Automatically track who updated/validated the image
        serializer.save(last_updated_by=self.request.user)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        asset = self.get_object()
        if not asset.image_file:
            return Response({'error': 'No image file available'}, status=404)
        return FileResponse(asset.image_file.open('rb'))

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


class BeetlesViewSet(viewsets.ModelViewSet):
    serializer_class = BeetlesSerializer
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        queryset = Beetles.objects.select_related(
            'image_asset',
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
        instance.delete(deleted_by=self.request.user)

    def perform_create(self, serializer):
        """
        Create a new bbox annotation or update an existing template beetle.
        Delegates all database writes strictly to serializer.save().
        """
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
                # 1. Look for a "Template/Ghost" ROI (has metadata, but NULL bbox coordinates)
                template_beetle = Beetles.objects.filter(
                    image_asset=image_asset,
                    bbox_x__isnull=True,
                    is_deleted=False
                ).first()

                if template_beetle:
                    # Instruct DRF to UPSERT: By setting serializer.instance, the subsequent 
                    # serializer.save() executes an SQL UPDATE instead of an INSERT.
                    serializer.instance = template_beetle
                else:
                    # 2. No Template found. Create a NEW ROI by copying metadata from the most recent existing ROI.
                    existing_beetle = Beetles.objects.filter(
                        image_asset=image_asset,
                        is_deleted=False
                    ).order_by('-last_updated_at').first()
                    
                    if existing_beetle:
                        save_kwargs.update({
                            'taxon': existing_beetle.taxon,  # Critical: Maintains materialized tree link
                            'aspect': existing_beetle.aspect,
                            'depicts_specimen': existing_beetle.depicts_specimen,
                            'depicts_valid_name_id': existing_beetle.depicts_valid_name_id,
                            'depicts_described_name_id': existing_beetle.depicts_described_name_id,
                            'depicts_name_verbatim': existing_beetle.depicts_name_verbatim,
                            'alternative_id': existing_beetle.alternative_id,
                            'collection_country': existing_beetle.collection_country,
                            'collection_stateProvince': existing_beetle.collection_stateProvince,
                            'specimen_sex': existing_beetle.specimen_sex,
                            'specimen_type_status': existing_beetle.specimen_type_status,
                            'specimen_notes': existing_beetle.specimen_notes,
                        })

        # 3. Terminal Execution: Execute the save exclusively through the serializer pipeline
        serializer.save(**save_kwargs)

    def perform_update(self, serializer):
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

        if serializer.validated_data.get('bbox_is_validated') == True:
            serializer.save(
                bbox_validated_by=self.request.user,
                bbox_validated_at=timezone.now(),
                last_updated_by=self.request.user
            )
        else:
            serializer.save(last_updated_by=self.request.user)

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
        try:
            # 2. Open a single database transaction
            with transaction.atomic():
                for item in data:
                    inst = instance_map.get(str(item.get('id')))
                    if inst:
                        # 3. Initialize serializer with partial=True for sparse updates
                        serializer = self.get_serializer(inst, data=item, partial=True)
                        serializer.is_valid(raise_exception=True)
                        
                        # 4. Route through perform_update to trigger your existing Validation/Ghost ROI logic!
                        self.perform_update(serializer)
                        
                        results.append(serializer.data)
                        
            return Response(results, status=status.HTTP_200_OK)
        except Exception as e:
            # If ANY serializer fails, the entire transaction rolls back
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='images-with-annotations')
    def images_with_annotations(self, request):
        """
        Unified API Feed for the Annotation Tool.
        """
        from django.db.models import Count, Q, Exists, OuterRef
        from django.core.paginator import Paginator
        from beetlesgallery.beetles_app.utils import build_query_q, filter_beetles_queryset, FILTERS_CONFIG

        page_num = int(request.GET.get('page', 1))
        page_size = min(int(request.GET.get('page_size', 50)), 200)
        ordering = request.GET.get('ordering', '-created_at')
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

        image_qs = ImageAsset.objects.filter(id__in=valid_image_ids, is_deleted=False).select_related('active_lock__locked_by')

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

        total_count = image_qs.count()

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

        if ordering == '-created_at':
            image_qs = image_qs.order_by('-created_at')
        else:
            image_qs = image_qs.order_by(ordering)

        paginator = Paginator(image_qs, page_size)
        page_obj = paginator.get_page(page_num)

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

            images.append({
                'image_asset_id': str(img.id),
                'beetle_id': str(img.specimens.first().id) if img.specimens.exists() else None, 
                'filename': os.path.basename(img.image_file.name) if img.image_file else 'unknown',
                'thumbnail_url': img.thumb_small.url if img.thumb_small else None,
                'full_image_url': img.display_url,
                'annotation_count': img.roi_count,
                'has_unvalidated_boxes': img.has_unvalidated_boxes,
                'is_validated': img.is_validated,
                'created_at': img.created_at.isoformat() if img.created_at else None,
                'lock': lock_info,
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
