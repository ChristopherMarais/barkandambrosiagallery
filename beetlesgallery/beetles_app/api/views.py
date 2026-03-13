from rest_framework import viewsets, status
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
    Permission class that checks if user is authenticated and is staff.
    """
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_staff


class ImageAssetViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for ImageAsset records.

    List all image assets or retrieve a single one by UUID.
    Supports download of original image files.
    """
    queryset = ImageAsset.objects.all()
    serializer_class = ImageAssetSerializer

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """
        Download the original image file.

        GET /api/v1/image-assets/{uuid}/download/
        Returns: Binary file stream
        """
        asset = self.get_object()

        if not asset.image_file:
            return Response({'error': 'No image file available'}, status=404)

        return FileResponse(asset.image_file.open('rb'))

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

        # Clean up expired locks first
        ImageLock.cleanup_expired_locks()

        # Check if image is already locked
        try:
            existing_lock = ImageLock.objects.select_related('locked_by').get(image_asset=asset)

            # If locked by current user, just refresh the timestamp
            if existing_lock.locked_by == user:
                existing_lock.save()  # Updates updated_at
                return Response({
                    'success': True,
                    'message': 'Lock refreshed',
                    'locked_by': user.username,
                    'locked_at': existing_lock.locked_at
                })

            # Locked by someone else - check if expired
            if existing_lock.is_expired():
                existing_lock.delete()
                # Create new lock below
            else:
                return Response({
                    'success': False,
                    'error': 'Image is currently locked by another user',
                    'locked_by': existing_lock.locked_by.username,
                    'locked_at': existing_lock.locked_at,
                    'locked_for_minutes': ImageLock.LOCK_TIMEOUT_MINUTES
                }, status=409)

        except ImageLock.DoesNotExist:
            pass  # No existing lock, create new one below

        # Create new lock
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
        ).all()

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

    def perform_create(self, serializer):
        if serializer.validated_data.get('bbox_x') is not None:
            image_asset_id = serializer.initial_data.get('image_asset_id')

            if image_asset_id:
                beetle_without_bbox = Beetles.objects.filter(
                    image_asset_id=image_asset_id,
                    bbox_x__isnull=True
                ).first()

                if beetle_without_bbox:
                    beetle_without_bbox.bbox_x = serializer.validated_data.get('bbox_x')
                    beetle_without_bbox.bbox_y = serializer.validated_data.get('bbox_y')
                    beetle_without_bbox.bbox_width = serializer.validated_data.get('bbox_width')
                    beetle_without_bbox.bbox_height = serializer.validated_data.get('bbox_height')
                    # bbox_label REMOVED
                    beetle_without_bbox.bbox_created_by = self.request.user
                    beetle_without_bbox.bbox_created_at = timezone.now()
                    beetle_without_bbox.save()

                    serializer.instance = beetle_without_bbox
                    return
                else:
                    template = Beetles.objects.filter(image_asset_id=image_asset_id).first()
                    if template:
                        extra_fields = {
                            'bbox_created_by': self.request.user,
                            'bbox_created_at': timezone.now(),
                            'depicts_valid_name_id': template.depicts_valid_name_id,
                            'depicts_specimen': template.depicts_specimen,
                            'depicts_name_verbatim': template.depicts_name_verbatim,
                            'collection_country': template.collection_country,
                            'collection_stateProvince': template.collection_stateProvince,
                            'specimen_sex': template.specimen_sex,
                            'specimen_type_status': template.specimen_type_status,
                        }
                        serializer.save(**extra_fields)
                        return

            serializer.save(
                bbox_created_by=self.request.user,
                bbox_created_at=timezone.now()
            )
        else:
            serializer.save()

    def perform_update(self, serializer):
        if 'bbox_x' in serializer.validated_data and serializer.validated_data.get('bbox_x') is None:
            instance = serializer.instance
            if instance and instance.bbox_x is not None:
                self._backup_deleted_beetle(instance)

            serializer.save(
                bbox_created_by=None,
                bbox_created_at=None,
                bbox_validated_by=None,
                bbox_validated_at=None,
                bbox_is_validated=False
            )
        elif serializer.validated_data.get('bbox_is_validated') == True:
            serializer.save(
                bbox_validated_by=self.request.user,
                bbox_validated_at=timezone.now()
            )
        else:
            serializer.save()

    def _backup_deleted_beetle(self, beetle):
        try:
            date_str = datetime.now().strftime('%Y-%m-%d')
            backup_dir = os.path.join(settings.MEDIA_ROOT, 'deleted_beetles', date_str)
            os.makedirs(backup_dir, exist_ok=True)

            serializer = BeetlesSerializer(beetle)
            beetle_data = serializer.data

            beetle_data['deleted_at'] = timezone.now().isoformat()
            beetle_data['deleted_by'] = self.request.user.username if self.request.user else None

            filepath = os.path.join(backup_dir, f"{beetle.id}.json")
            with open(filepath, 'w') as f:
                json.dump(beetle_data, f, indent=2)

            return filepath
        except Exception as e:
            print(f"Warning: Failed to backup beetle {beetle.id}: {str(e)}")
            return None

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self._backup_deleted_beetle(instance)
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='images-with-annotations')
    def images_with_annotations(self, request):
        """
        Unified API Feed for the Annotation Tool.
        Returns a paginated list of ImageAssets and respects all advanced filters.
        """
        from django.db.models import Count, Q, Exists, OuterRef
        from django.core.paginator import Paginator
        from beetlesgallery.beetles_app.utils import build_query_q, filter_beetles_queryset, FILTERS_CONFIG

        page_num = int(request.GET.get('page', 1))
        page_size = min(int(request.GET.get('page_size', 50)), 200)
        ordering = request.GET.get('ordering', '-created_at')
        search = request.GET.get('search', '').strip()

        # Step 1: Base Beetles Query (Apply text search and advanced filters)
        beetles_qs = Beetles.objects.all()

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

        # Get valid image IDs that survived the beetle-level filters
        valid_image_ids = beetles_qs.values('image_asset_id')

        # Step 2: Query ImageAssets directly for clean pagination and grouping
        image_qs = ImageAsset.objects.filter(id__in=valid_image_ids).select_related('active_lock__locked_by')

        unvalidated_rois = Beetles.objects.filter(
            image_asset_id=OuterRef('pk'),
            bbox_x__isnull=False,
            bbox_is_validated=False
        )

        image_qs = image_qs.annotate(
            roi_count=Count('specimens__id', filter=Q(specimens__bbox_x__isnull=False), distinct=True),
            has_unvalidated_boxes=Exists(unvalidated_rois)
        )

        total_count = image_qs.count()

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
                # If no specimens exist, beetle_id can be left null
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

        # Pagination URLs
        base_url = request.build_absolute_uri(request.path)
        next_url = None
        prev_url = None
        
        # Reconstruct query string for pagination links
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
            'results': images
        })


class SpeciesViewSet(viewsets.ViewSet):
    """
    API endpoint for species from the native Postgres Taxon table.
    """
    serializer_class = SpeciesSerializer

    def list(self, request):
        from beetlesgallery.beetles_app.models import Taxon
        
        subfamily = request.query_params.get('subfamily')
        genus = request.query_params.get('genus')
        tribe = request.query_params.get('tribe')
        species_param = request.query_params.get('species')

        qs = Taxon.objects.all()

        if subfamily:
            qs = qs.filter(subfamily__iexact=subfamily)
        if genus:
            qs = qs.filter(genus__iexact=genus)
        if tribe:
            qs = qs.filter(tribe__iexact=tribe)
        if species_param:
            qs = qs.filter(species__iexact=species_param)

        results = []
        for t in qs:
            results.append({
                "valid_species_id": t.valid_species_id,
                "scientificName": t.scientific_name,
                "scientificNameAuthority": t.scientific_name_authority,
                "subfamily": t.subfamily,
                "tribe": t.tribe,
                "subtribe": t.subtribe,
                "genus": t.genus,
                "species": t.species,
                "subspecies": t.subspecies,
                "authority": t.authority,
                "authorityYear": t.authority_year,
                "originalGenus": t.original_genus,
            })

        serializer = SpeciesSerializer(results, many=True)
        return Response(serializer.data)
