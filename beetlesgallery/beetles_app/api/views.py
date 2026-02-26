from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.http import FileResponse
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from beetlesgallery.beetles_app.models import ImageAsset, Beetles
from beetlesgallery.beetles_app import species_ref
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


class BeetlesViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Beetles (specimen) records.

    Now also handles bounding box annotations - each Beetle can represent:
    1. A specimen metadata record (bbox_x is NULL)
    2. A bounding box annotation on an image (bbox_x is NOT NULL)

    Supports filtering by:
    - subfamily: GET /api/v1/beetles/?subfamily=Platypodinae
    - image_asset: GET /api/v1/beetles/?image_asset=<uuid>
    - has_bbox: GET /api/v1/beetles/?has_bbox=true|false
    """
    serializer_class = BeetlesSerializer
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        """
        Filter beetles by subfamily, image_asset, or bbox status.
        """
        queryset = Beetles.objects.select_related(
            'image_asset',
            'bbox_created_by',
            'bbox_validated_by'
        ).all()

        # Filter by subfamily if provided
        subfamily = self.request.query_params.get('subfamily')
        if subfamily:
            # Look up all valid_species_ids that match this subfamily
            try:
                matching_ids = species_ref.ids_for('subfamily', subfamily)
                if matching_ids:
                    queryset = queryset.filter(depicts_valid_name_id__in=matching_ids)
                else:
                    queryset = queryset.none()
            except Exception:
                queryset = queryset.none()

        # Filter by image_asset
        image_asset_id = self.request.query_params.get('image_asset')
        if image_asset_id:
            queryset = queryset.filter(image_asset_id=image_asset_id)

        # Filter by bbox status
        has_bbox = self.request.query_params.get('has_bbox')
        if has_bbox == 'true':
            queryset = queryset.exclude(bbox_x__isnull=True)
        elif has_bbox == 'false':
            queryset = queryset.filter(bbox_x__isnull=True)

        return queryset

    def perform_create(self, serializer):
        """
        Set bbox_created_by to current user if bbox data is present.
        If creating a bbox annotation, either update existing beetle or create new one.

        Logic:
        - Find beetle on this image WITHOUT bbox coordinates → update it with new bbox
        - If all beetles on this image already have bbox → create new beetle with copied metadata + new bbox

        NOTE: This runs BEFORE serializer.save() and serializer.create(),
        so image_asset hasn't been resolved yet - we only have image_asset_id.
        """
        # If this is a bbox annotation (has bbox_x)
        if serializer.validated_data.get('bbox_x') is not None:
            # Get image_asset_id from initial_data (what was sent from frontend)
            image_asset_id = serializer.initial_data.get('image_asset_id')

            if image_asset_id:
                # Find beetle without bbox on this image
                beetle_without_bbox = Beetles.objects.filter(
                    image_asset_id=image_asset_id,
                    bbox_x__isnull=True
                ).first()

                if beetle_without_bbox:
                    # Update existing beetle record with bbox data
                    beetle_without_bbox.bbox_x = serializer.validated_data.get('bbox_x')
                    beetle_without_bbox.bbox_y = serializer.validated_data.get('bbox_y')
                    beetle_without_bbox.bbox_width = serializer.validated_data.get('bbox_width')
                    beetle_without_bbox.bbox_height = serializer.validated_data.get('bbox_height')
                    beetle_without_bbox.bbox_label = serializer.validated_data.get('bbox_label')
                    beetle_without_bbox.bbox_created_by = self.request.user
                    beetle_without_bbox.bbox_created_at = timezone.now()
                    beetle_without_bbox.save()

                    # Return the updated beetle instead of creating new
                    serializer.instance = beetle_without_bbox
                    return
                else:
                    # All beetles have bbox - find one to copy metadata from
                    template = Beetles.objects.filter(
                        image_asset_id=image_asset_id
                    ).first()

                    if template:
                        # Create new beetle with copied metadata
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

            # No template found or no image_asset_id - just save with bbox audit fields
            serializer.save(
                bbox_created_by=self.request.user,
                bbox_created_at=timezone.now()
            )
        else:
            serializer.save()

    def perform_update(self, serializer):
        """
        Handle bbox validation and clearing:
        - Set bbox_validated_by and bbox_validated_at when bbox_is_validated is set to true
        - Clear bbox_created_by, bbox_created_at, bbox_validated_by, bbox_validated_at when bbox is cleared
        - Backup beetle record when bbox is being cleared
        """
        # Check if bbox is being cleared (bbox_x is null)
        if 'bbox_x' in serializer.validated_data and serializer.validated_data.get('bbox_x') is None:
            # Backup the beetle record before clearing bbox data
            instance = serializer.instance
            if instance and instance.bbox_x is not None:  # Only backup if it currently has bbox data
                self._backup_deleted_beetle(instance)

            # Clear all bbox audit fields
            serializer.save(
                bbox_created_by=None,
                bbox_created_at=None,
                bbox_validated_by=None,
                bbox_validated_at=None,
                bbox_is_validated=False
            )
        # If bbox_is_validated is being set to true, auto-fill validation audit fields
        elif serializer.validated_data.get('bbox_is_validated') == True:
            serializer.save(
                bbox_validated_by=self.request.user,
                bbox_validated_at=timezone.now()
            )
        else:
            serializer.save()

    def _backup_deleted_beetle(self, beetle):
        """
        Backup a beetle record to JSON file before deletion.
        Saves to media/deleted_beetles/<date>/<beetle_id>.json
        """
        try:
            # Create backup directory structure
            date_str = datetime.now().strftime('%Y-%m-%d')
            backup_dir = os.path.join(settings.MEDIA_ROOT, 'deleted_beetles', date_str)
            os.makedirs(backup_dir, exist_ok=True)

            # Serialize beetle data
            serializer = BeetlesSerializer(beetle)
            beetle_data = serializer.data

            # Add deletion metadata
            beetle_data['deleted_at'] = timezone.now().isoformat()
            beetle_data['deleted_by'] = self.request.user.username if self.request.user else None

            # Write to JSON file
            filename = f"{beetle.id}.json"
            filepath = os.path.join(backup_dir, filename)

            with open(filepath, 'w') as f:
                json.dump(beetle_data, f, indent=2)

            return filepath
        except Exception as e:
            # Log error but don't fail the deletion
            print(f"Warning: Failed to backup beetle {beetle.id}: {str(e)}")
            return None

    def destroy(self, request, *args, **kwargs):
        """
        Override destroy to backup beetle record before deletion.
        """
        instance = self.get_object()

        # Backup the beetle record
        self._backup_deleted_beetle(instance)

        # Proceed with deletion
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['post'], url_path='import-annotations')
    def import_annotations(self, request):
        """
        Bulk import annotations from YOLO or COCO format.
        Creates new Beetle records with bbox data.

        POST /api/v1/beetles/import-annotations/

        Form data:
        - file: ZIP file containing annotation files
        - format: 'yolo' or 'coco'
        - overwrite: true/false - whether to delete existing annotations (default: false)

        Returns:
        {
            "success": true,
            "imported_count": 42,
            "skipped_count": 3,
            "errors": [],
            "details": {...}
        }
        """
        zip_file = request.FILES.get('file')
        annotation_format = request.data.get('format', 'yolo').lower()
        overwrite = request.data.get('overwrite', 'false').lower() == 'true'

        if not zip_file:
            return Response(
                {'error': 'No file provided. Please upload a ZIP file.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if annotation_format not in ['yolo', 'coco']:
            return Response(
                {'error': 'Invalid format. Must be "yolo" or "coco".'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Parse the ZIP file
            with zipfile.ZipFile(zip_file, 'r') as zf:
                results = self._process_annotation_zip(
                    zf, annotation_format, overwrite, request.user
                )

            return Response(results, status=status.HTTP_201_CREATED)

        except zipfile.BadZipFile:
            return Response(
                {'error': 'Invalid ZIP file.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': f'Import failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _process_annotation_zip(self, zip_file, annotation_format, overwrite, user):
        """
        Process annotation files from ZIP and create Beetle records with bbox data.
        Each bounding box becomes a NEW Beetle record.
        """
        imported_count = 0
        skipped_count = 0
        errors = []
        details = {}

        file_list = [f for f in zip_file.namelist() if not f.startswith('__MACOSX') and not f.endswith('/')]

        # Special handling for COCO format with single annotations.json file
        if annotation_format == 'coco' and 'annotations.json' in file_list:
            return self._process_coco_all_annotations(zip_file, overwrite, user)

        with transaction.atomic():
            for filename in file_list:
                try:
                    # Extract beetle UUID from filename (remove extension)
                    beetle_uuid = os.path.splitext(os.path.basename(filename))[0]

                    # Look up the Beetle record to get the image_asset
                    try:
                        lookup_beetle = Beetles.objects.select_related('image_asset').get(pk=beetle_uuid)
                    except Beetles.DoesNotExist:
                        skipped_count += 1
                        errors.append(f'{filename}: No beetle found with UUID {beetle_uuid}')
                        continue

                    if not lookup_beetle.image_asset:
                        skipped_count += 1
                        errors.append(f'{filename}: Beetle {beetle_uuid} has no associated image')
                        continue

                    image_asset = lookup_beetle.image_asset

                    # Find template beetle (beetle WITHOUT bbox data on this image) for metadata copying
                    template_beetle = Beetles.objects.filter(
                        image_asset=image_asset,
                        bbox_x__isnull=True
                    ).first()

                    if not template_beetle:
                        skipped_count += 1
                        errors.append(f'{filename}: No template beetle (without bbox) found for image')
                        continue

                    # Check if bbox annotations already exist for this image
                    existing_count = Beetles.objects.filter(
                        image_asset=image_asset
                    ).exclude(bbox_x__isnull=True).count()

                    if existing_count > 0 and not overwrite:
                        skipped_count += 1
                        errors.append(
                            f'{filename}: Image already has {existing_count} annotation(s). '
                            f'Enable "Overwrite existing annotations" to replace them.'
                        )
                        continue

                    # Read annotation file content
                    content = zip_file.read(filename).decode('utf-8')

                    # Parse annotations based on format
                    if annotation_format == 'yolo':
                        result = self._parse_yolo_to_beetles(
                            content, image_asset, template_beetle, user
                        )
                    else:  # coco
                        result = self._parse_coco_to_beetles(
                            content, image_asset, template_beetle, user
                        )

                    # Handle result: dict with updates + new beetles, or list of new beetles
                    if isinstance(result, dict):
                        # Updated existing beetle + possibly new beetles to create
                        boxes_updated = result.get('boxes_updated', 0)
                        beetles_to_create = result.get('beetles_to_create', [])

                        # Delete existing bbox annotations if overwrite is True
                        if overwrite and existing_count > 0:
                            Beetles.objects.filter(
                                image_asset=image_asset
                            ).exclude(bbox_x__isnull=True).delete()

                        # Bulk create new beetle records
                        if beetles_to_create:
                            Beetles.objects.bulk_create(beetles_to_create)

                        total_imported = boxes_updated + len(beetles_to_create)
                        imported_count += total_imported
                        details[beetle_uuid] = {
                            'filename': filename,
                            'boxes_imported': total_imported,
                            'replaced': existing_count if overwrite else 0,
                            'updated_existing': boxes_updated > 0
                        }
                    else:
                        # All new beetles to create
                        beetle_records = result

                        # Delete existing bbox annotations if overwrite is True
                        if overwrite and existing_count > 0:
                            Beetles.objects.filter(
                                image_asset=image_asset
                            ).exclude(bbox_x__isnull=True).delete()

                        # Bulk create beetle records
                        if beetle_records:
                            Beetles.objects.bulk_create(beetle_records)
                            imported_count += len(beetle_records)
                            details[beetle_uuid] = {
                                'filename': filename,
                                'boxes_imported': len(beetle_records),
                                'replaced': existing_count if overwrite else 0
                            }
                        else:
                            skipped_count += 1
                            errors.append(f'{filename}: No valid bounding boxes found')

                except Exception as e:
                    skipped_count += 1
                    errors.append(f'{filename}: {str(e)}')
                    continue

        return {
            'success': True,
            'imported_count': imported_count,
            'skipped_count': skipped_count,
            'files_processed': len(file_list),
            'errors': errors,
            'details': details
        }

    def _parse_yolo_to_beetles(self, content, image_asset, template_beetle, user):
        """
        Parse YOLO format and either update existing beetle or create new beetles.

        Logic:
        - Find beetle without bbox on this image → update it with first box
        - If all beetles have bbox → create new beetles with copied metadata

        Returns:
        - dict with 'boxes_updated' and 'boxes_created' counts
        - list of new Beetle instances to bulk create
        """
        import uuid as uuid_lib

        # Parse all bounding boxes from file
        parsed_boxes = []
        for line in content.strip().split('\n'):
            line = line.strip()
            if not line:
                continue

            try:
                parts = line.split()
                if len(parts) < 5:
                    continue

                label = parts[0]
                x_center = float(parts[1])
                y_center = float(parts[2])
                width = float(parts[3])
                height = float(parts[4])

                # Convert center coords to top-left coords
                x = x_center - (width / 2)
                y = y_center - (height / 2)

                # Validate coordinates
                if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
                    continue
                if x + width > 1.0001 or y + height > 1.0001:
                    continue

                parsed_boxes.append({
                    'x': x,
                    'y': y,
                    'width': width,
                    'height': height,
                    'label': label
                })

            except (ValueError, IndexError):
                continue

        if not parsed_boxes:
            return []

        boxes_updated = 0
        beetles_to_create = []

        # Process each box: find beetle without bbox or create new one
        for box in parsed_boxes:
            # Find beetle without bbox on this image
            beetle_without_bbox = Beetles.objects.filter(
                image_asset=image_asset,
                bbox_x__isnull=True
            ).first()

            if beetle_without_bbox:
                # Update existing beetle with this box
                beetle_without_bbox.bbox_x = box['x']
                beetle_without_bbox.bbox_y = box['y']
                beetle_without_bbox.bbox_width = box['width']
                beetle_without_bbox.bbox_height = box['height']
                beetle_without_bbox.bbox_label = box['label']
                beetle_without_bbox.bbox_created_by = user
                beetle_without_bbox.bbox_created_at = timezone.now()
                beetle_without_bbox.save()
                boxes_updated += 1
            else:
                # No beetle without bbox - create new beetle
                beetle = Beetles(
                    id=uuid_lib.uuid4(),
                    image_asset=image_asset,
                    # Copy specimen metadata from template
                    depicts_valid_name_id=template_beetle.depicts_valid_name_id,
                    depicts_specimen=template_beetle.depicts_specimen,
                    depicts_name_verbatim=template_beetle.depicts_name_verbatim,
                    collection_country=template_beetle.collection_country,
                    collection_stateProvince=template_beetle.collection_stateProvince,
                    specimen_sex=template_beetle.specimen_sex,
                    specimen_type_status=template_beetle.specimen_type_status,
                    # Bbox data
                    bbox_x=box['x'],
                    bbox_y=box['y'],
                    bbox_width=box['width'],
                    bbox_height=box['height'],
                    bbox_label=box['label'],
                    bbox_created_by=user,
                    bbox_created_at=timezone.now()
                )
                beetles_to_create.append(beetle)

        if boxes_updated > 0:
            return {'boxes_updated': boxes_updated, 'beetles_to_create': beetles_to_create}
        else:
            return beetles_to_create

    def _parse_coco_to_beetles(self, content, image_asset, template_beetle, user):
        """
        Parse COCO format and either update existing beetle or create new beetles.

        Logic:
        - Find beetle without bbox on this image → update it with first box
        - If all beetles have bbox → create new beetles with copied metadata

        Returns:
        - dict with 'boxes_updated' and 'beetles_to_create' counts
        - list of new Beetle instances to bulk create
        """
        import uuid as uuid_lib

        try:
            data = json.loads(content)

            # Find image info
            image_info = None
            if 'images' in data and len(data['images']) > 0:
                image_info = data['images'][0]

            # Get image dimensions
            if image_info and 'width' in image_info and 'height' in image_info:
                img_width = image_info['width']
                img_height = image_info['height']
            elif image_asset.image_width and image_asset.image_height:
                img_width = image_asset.image_width
                img_height = image_asset.image_height
            else:
                return []

            # Parse annotations
            annotations = data.get('annotations', [])
            parsed_boxes = []

            for ann in annotations:
                bbox = ann.get('bbox')
                if not bbox or len(bbox) < 4:
                    continue

                # COCO bbox format: [x, y, width, height] in pixels
                x_px = bbox[0]
                y_px = bbox[1]
                w_px = bbox[2]
                h_px = bbox[3]

                # Convert to normalized coordinates
                x = x_px / img_width
                y = y_px / img_height
                width = w_px / img_width
                height = h_px / img_height

                # Validate coordinates
                if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
                    continue
                if x + width > 1.0001 or y + height > 1.0001:
                    continue

                label = str(ann.get('category_id', ''))

                parsed_boxes.append({
                    'x': x,
                    'y': y,
                    'width': width,
                    'height': height,
                    'label': label
                })

            if not parsed_boxes:
                return []

            boxes_updated = 0
            beetles_to_create = []

            # Process each box: find beetle without bbox or create new one
            for box in parsed_boxes:
                # Find beetle without bbox on this image
                beetle_without_bbox = Beetles.objects.filter(
                    image_asset=image_asset,
                    bbox_x__isnull=True
                ).first()

                if beetle_without_bbox:
                    # Update existing beetle with this box
                    beetle_without_bbox.bbox_x = box['x']
                    beetle_without_bbox.bbox_y = box['y']
                    beetle_without_bbox.bbox_width = box['width']
                    beetle_without_bbox.bbox_height = box['height']
                    beetle_without_bbox.bbox_label = box['label']
                    beetle_without_bbox.bbox_created_by = user
                    beetle_without_bbox.bbox_created_at = timezone.now()
                    beetle_without_bbox.save()
                    boxes_updated += 1
                else:
                    # No beetle without bbox - create new beetle
                    beetle = Beetles(
                        id=uuid_lib.uuid4(),
                        image_asset=image_asset,
                        # Copy specimen metadata from template
                        depicts_valid_name_id=template_beetle.depicts_valid_name_id,
                        depicts_specimen=template_beetle.depicts_specimen,
                        depicts_name_verbatim=template_beetle.depicts_name_verbatim,
                        collection_country=template_beetle.collection_country,
                        collection_stateProvince=template_beetle.collection_stateProvince,
                        specimen_sex=template_beetle.specimen_sex,
                        specimen_type_status=template_beetle.specimen_type_status,
                        # Bbox data
                        bbox_x=box['x'],
                        bbox_y=box['y'],
                        bbox_width=box['width'],
                        bbox_height=box['height'],
                        bbox_label=box['label'],
                        bbox_created_by=user,
                        bbox_created_at=timezone.now()
                    )
                    beetles_to_create.append(beetle)

            if boxes_updated > 0:
                return {'boxes_updated': boxes_updated, 'beetles_to_create': beetles_to_create}
            else:
                return beetles_to_create

        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    def _process_coco_all_annotations(self, zip_file, overwrite, user):
        """Process a single COCO annotations.json file."""
        import uuid as uuid_lib
        imported_count = 0
        skipped_count = 0
        errors = []
        details = {}

        try:
            content = zip_file.read('annotations.json').decode('utf-8')
            data = json.loads(content)

            images = data.get('images', [])
            annotations = data.get('annotations', [])

            # Build map of image_id to annotations
            annotations_by_image = {}
            for ann in annotations:
                image_id = ann.get('image_id')
                if image_id not in annotations_by_image:
                    annotations_by_image[image_id] = []
                annotations_by_image[image_id].append(ann)

            with transaction.atomic():
                for image_data in images:
                    try:
                        image_id = image_data.get('id')
                        file_name = image_data.get('file_name', '')
                        img_width = image_data.get('width')
                        img_height = image_data.get('height')

                        beetle_uuid = os.path.splitext(file_name)[0]

                        try:
                            lookup_beetle = Beetles.objects.select_related('image_asset').get(pk=beetle_uuid)
                        except Beetles.DoesNotExist:
                            skipped_count += 1
                            errors.append(f'{file_name}: No beetle found with UUID {beetle_uuid}')
                            continue

                        if not lookup_beetle.image_asset:
                            skipped_count += 1
                            errors.append(f'{file_name}: Beetle {beetle_uuid} has no associated image')
                            continue

                        image_asset = lookup_beetle.image_asset

                        # Find template beetle (beetle WITHOUT bbox data on this image) for metadata copying
                        template_beetle = Beetles.objects.filter(
                            image_asset=image_asset,
                            bbox_x__isnull=True
                        ).first()

                        if not template_beetle:
                            skipped_count += 1
                            errors.append(f'{file_name}: No template beetle (without bbox) found for image')
                            continue

                        existing_count = Beetles.objects.filter(
                            image_asset=image_asset
                        ).exclude(bbox_x__isnull=True).count()

                        if existing_count > 0 and not overwrite:
                            skipped_count += 1
                            errors.append(
                                f'{file_name}: Image already has {existing_count} annotation(s).'
                            )
                            continue

                        image_annotations = annotations_by_image.get(image_id, [])

                        # Parse all boxes for this image
                        parsed_boxes = []
                        for ann in image_annotations:
                            bbox = ann.get('bbox')
                            if not bbox or len(bbox) < 4:
                                continue

                            x_px, y_px, w_px, h_px = bbox[0], bbox[1], bbox[2], bbox[3]

                            if img_width and img_height:
                                x = x_px / img_width
                                y = y_px / img_height
                                width = w_px / img_width
                                height = h_px / img_height
                            elif image_asset.image_width and image_asset.image_height:
                                x = x_px / image_asset.image_width
                                y = y_px / image_asset.image_height
                                width = w_px / image_asset.image_width
                                height = h_px / image_asset.image_height
                            else:
                                continue

                            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
                                continue
                            if x + width > 1.0001 or y + height > 1.0001:
                                continue

                            label = str(ann.get('category_id', ''))

                            parsed_boxes.append({
                                'x': x,
                                'y': y,
                                'width': width,
                                'height': height,
                                'label': label
                            })

                        if not parsed_boxes:
                            skipped_count += 1
                            errors.append(f'{file_name}: No valid bounding boxes found')
                            continue

                        # Delete existing bbox annotations if overwrite is True (BEFORE processing)
                        if overwrite and existing_count > 0:
                            Beetles.objects.filter(
                                image_asset=image_asset
                            ).exclude(bbox_x__isnull=True).delete()

                        boxes_updated = 0
                        beetles_to_create = []

                        # Process each box: find beetle without bbox or create new one
                        for box in parsed_boxes:
                            # Find beetle without bbox on this image
                            beetle_without_bbox = Beetles.objects.filter(
                                image_asset=image_asset,
                                bbox_x__isnull=True
                            ).first()

                            if beetle_without_bbox:
                                # Update existing beetle with this box
                                beetle_without_bbox.bbox_x = box['x']
                                beetle_without_bbox.bbox_y = box['y']
                                beetle_without_bbox.bbox_width = box['width']
                                beetle_without_bbox.bbox_height = box['height']
                                beetle_without_bbox.bbox_label = box['label']
                                beetle_without_bbox.bbox_created_by = user
                                beetle_without_bbox.bbox_created_at = timezone.now()
                                beetle_without_bbox.save()
                                boxes_updated += 1
                            else:
                                # No beetle without bbox - create new beetle
                                beetle = Beetles(
                                    id=uuid_lib.uuid4(),
                                    image_asset=image_asset,
                                    depicts_valid_name_id=template_beetle.depicts_valid_name_id,
                                    depicts_specimen=template_beetle.depicts_specimen,
                                    depicts_name_verbatim=template_beetle.depicts_name_verbatim,
                                    collection_country=template_beetle.collection_country,
                                    collection_stateProvince=template_beetle.collection_stateProvince,
                                    specimen_sex=template_beetle.specimen_sex,
                                    specimen_type_status=template_beetle.specimen_type_status,
                                    bbox_x=box['x'],
                                    bbox_y=box['y'],
                                    bbox_width=box['width'],
                                    bbox_height=box['height'],
                                    bbox_label=box['label'],
                                    bbox_created_by=user,
                                    bbox_created_at=timezone.now()
                                )
                                beetles_to_create.append(beetle)

                        # Bulk create new beetle records
                        if beetles_to_create:
                            Beetles.objects.bulk_create(beetles_to_create)

                        total_imported = boxes_updated + len(beetles_to_create)
                        imported_count += total_imported
                        details[beetle_uuid] = {
                            'filename': file_name,
                            'boxes_imported': total_imported,
                            'replaced': existing_count if overwrite else 0,
                            'updated_existing': boxes_updated > 0
                        }

                    except Exception as e:
                        skipped_count += 1
                        errors.append(f'{file_name}: {str(e)}')
                        continue

        except Exception as e:
            return {
                'success': False,
                'imported_count': 0,
                'skipped_count': 0,
                'files_processed': 0,
                'errors': [f'Failed to parse annotations.json: {str(e)}'],
                'details': {}
            }

        return {
            'success': True,
            'imported_count': imported_count,
            'skipped_count': skipped_count,
            'files_processed': len(images),
            'errors': errors,
            'details': details
        }

    @action(detail=False, methods=['get'], url_path='images-with-annotations')
    def images_with_annotations(self, request):
        """
        Get paginated list of images with bounding box annotations.

        GET /api/v1/beetles/images-with-annotations/
        """
        from django.db.models import Count, Q, Exists, OuterRef, Prefetch
        from django.core.paginator import Paginator

        page_num = int(request.GET.get('page', 1))
        page_size = min(int(request.GET.get('page_size', 50)), 200)
        validation_status = request.GET.get('validation_status', 'all')
        ordering = request.GET.get('ordering', '-created_at')
        search = request.GET.get('search', '').strip()

        # Build query for beetles with bbox data
        unvalidated_subquery = Beetles.objects.filter(
            image_asset_id=OuterRef('image_asset_id'),
            bbox_is_validated=False
        ).exclude(bbox_x__isnull=True)

        base_query = Beetles.objects.exclude(bbox_x__isnull=True).values('image_asset_id').annotate(
            box_count=Count('id'),
            has_unvalidated=Exists(unvalidated_subquery)
        ).filter(box_count__gt=0)

        if search:
            matching_image_assets = ImageAsset.objects.filter(
                Q(image_file__icontains=search) | Q(id__icontains=search)
            ).values_list('id', flat=True)

            matching_beetles = Beetles.objects.filter(
                Q(id__icontains=search)
            ).values_list('image_asset_id', flat=True)

            matching_ids = set(matching_image_assets) | set(matching_beetles)
            base_query = base_query.filter(image_asset_id__in=matching_ids)

        if validation_status == 'unvalidated':
            base_query = base_query.filter(has_unvalidated=True)
        elif validation_status == 'validated':
            base_query = base_query.filter(has_unvalidated=False)

        total_count = base_query.count()

        if ordering not in ['-created_at', 'created_at']:
            base_query = base_query.order_by(ordering)

        paginator = Paginator(list(base_query), page_size)
        page_obj = paginator.get_page(page_num)

        image_asset_ids = [item['image_asset_id'] for item in page_obj.object_list]

        image_assets = ImageAsset.objects.filter(
            id__in=image_asset_ids
        ).prefetch_related(
            Prefetch('specimens', queryset=Beetles.objects.all())
        )

        image_asset_dict = {str(img.id): img for img in image_assets}
        box_counts = {item['image_asset_id']: item for item in page_obj.object_list}

        images = []
        for image_asset_id in image_asset_ids:
            image_asset_id_str = str(image_asset_id)
            image_asset = image_asset_dict.get(image_asset_id_str)

            if not image_asset:
                continue

            beetle = image_asset.specimens.first()
            if not beetle:
                continue

            box_info = box_counts[image_asset_id]

            images.append({
                'image_asset_id': image_asset_id_str,
                'beetle_id': str(beetle.id),
                'filename': os.path.basename(image_asset.image_file.name) if image_asset.image_file else 'unknown',
                'thumbnail_url': image_asset.thumb_small.url if image_asset.thumb_small else None,
                'full_image_url': image_asset.display_url,
                'annotation_count': box_info['box_count'],
                'has_unvalidated_boxes': box_info['has_unvalidated'],
                'created_at': beetle.last_updated_at.isoformat() if beetle.last_updated_at else None,
            })

        if ordering in ['-created_at', 'created_at']:
            reverse = ordering.startswith('-')
            images.sort(key=lambda x: x['created_at'] or '', reverse=reverse)

        base_url = request.build_absolute_uri(request.path)
        next_url = None
        prev_url = None

        if page_obj.has_next():
            next_url = f"{base_url}?page={page_obj.next_page_number()}&page_size={page_size}&validation_status={validation_status}&ordering={ordering}"
            if search:
                next_url += f"&search={search}"

        if page_obj.has_previous():
            prev_url = f"{base_url}?page={page_obj.previous_page_number()}&page_size={page_size}&validation_status={validation_status}&ordering={ordering}"
            if search:
                prev_url += f"&search={search}"

        return Response({
            'count': total_count,
            'next': next_url,
            'previous': prev_url,
            'results': images
        })

    @action(detail=False, methods=['get'], url_path='images-without-annotations')
    def images_without_annotations(self, request):
        """
        Get paginated list of images WITHOUT any bounding box annotations.

        GET /api/v1/beetles/images-without-annotations/
        """
        from django.core.paginator import Paginator
        from django.db.models import Exists, OuterRef, Q

        page_num = int(request.GET.get('page', 1))
        page_size = min(int(request.GET.get('page_size', 50)), 200)
        search = request.GET.get('search', '').strip()

        # Subquery to check if image has any bbox annotations
        has_bbox_subquery = Beetles.objects.filter(
            image_asset_id=OuterRef('pk')
        ).exclude(bbox_x__isnull=True)

        images_without_boxes = ImageAsset.objects.annotate(
            has_boxes=Exists(has_bbox_subquery)
        ).filter(
            has_boxes=False,
            image_file__isnull=False
        )

        if search:
            matching_beetles = Beetles.objects.filter(
                Q(id__icontains=search)
            ).values_list('image_asset_id', flat=True)

            images_without_boxes = images_without_boxes.filter(
                Q(image_file__icontains=search) |
                Q(id__icontains=search) |
                Q(id__in=matching_beetles)
            )

        images_without_boxes = images_without_boxes.order_by('-created_at')
        total_count = images_without_boxes.count()

        paginator = Paginator(images_without_boxes, page_size)
        page_obj = paginator.get_page(page_num)

        image_assets = list(page_obj.object_list)

        images = []
        for image_asset in image_assets:
            beetle = image_asset.specimens.first()

            images.append({
                'image_asset_id': str(image_asset.id),
                'beetle_id': str(beetle.id) if beetle else None,
                'filename': os.path.basename(image_asset.image_file.name) if image_asset.image_file else 'unknown',
                'thumbnail_url': image_asset.thumb_small.url if image_asset.thumb_small else None,
                'full_image_url': image_asset.display_url,
                'annotation_count': 0,
                'has_unvalidated_boxes': False,
                'created_at': image_asset.created_at.isoformat() if image_asset.created_at else None,
            })

        base_url = request.build_absolute_uri(request.path)
        next_url = None
        prev_url = None

        if page_obj.has_next():
            next_url = f"{base_url}?page={page_obj.next_page_number()}&page_size={page_size}"
            if search:
                next_url += f"&search={search}"

        if page_obj.has_previous():
            prev_url = f"{base_url}?page={page_obj.previous_page_number()}&page_size={page_size}"
            if search:
                prev_url += f"&search={search}"

        return Response({
            'count': total_count,
            'next': next_url,
            'previous': prev_url,
            'results': images
        })

    @action(detail=False, methods=['get'], url_path='category-mapping')
    def category_mapping(self, request):
        """
        Get the category mapping for bounding box labels.

        GET /api/v1/beetles/category-mapping/
        """
        from django.conf import settings
        from pathlib import Path

        mapping_path = Path(settings.MEDIA_ROOT) / 'reference' / 'category_mapping.json'

        if not mapping_path.exists():
            return Response(
                {'error': 'Category mapping file not found. Please run: python manage.py generate_category_mapping'},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            with open(mapping_path, 'r', encoding='utf-8') as f:
                data = json.loads(f.read())

            categories = data.get('categories', [])

            search = request.query_params.get('search', '').strip().lower()
            if search:
                categories = [
                    cat for cat in categories
                    if search in cat['name'].lower() or
                       (cat['genus'] and search in cat['genus'].lower()) or
                       (cat['species'] and search in cat['species'].lower())
                ]

            return Response({
                'version': data.get('version'),
                'total_categories': len(categories),
                'categories': categories
            })

        except Exception as e:
            return Response(
                {'error': f'Failed to load category mapping: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='taxonomy')
    def taxonomy(self, request):
        """
        Get taxonomy data for a given valid_name_id using species_ref.resolve().

        GET /api/v1/beetles/taxonomy/?valid_name_id=123

        Returns:
        {
            "scientificName": "...",
            "authority": "...",
            "subfamily": "...",
            "tribe": "...",
            "subtribe": "...",
            "genus": "...",
            "species": "...",
            "subspecies": "...",
            "authorityYear": "...",
            "originalGenus": "..."
        }
        """
        import math

        valid_name_id = request.query_params.get('valid_name_id', '').strip()

        if not valid_name_id:
            return Response(
                {'error': 'valid_name_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Normalize the ID (same logic as in main views.py)
        def normalize_id(v):
            if v is None:
                return None
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return None
            else:
                s = str(v).strip()
            try:
                f = float(s)
                if math.isnan(f):
                    return None
                if f.is_integer():
                    return str(int(f))
            except ValueError:
                return s
            return s

        norm_vid = normalize_id(valid_name_id)

        if norm_vid is None:
            return Response({})

        # Resolve taxonomy using species_ref
        ref_species = species_ref.resolve(norm_vid)

        if not ref_species:
            return Response({})

        return Response(ref_species)


class SpeciesViewSet(viewsets.ViewSet):
    """
    API endpoint for species from valid_species.csv.

    Returns ALL species matching the filter (whether they have images or not).
    For species with images, includes the image data nested under 'images' field.

    Supports filtering by:
    GET /api/v1/species/?subfamily=Platypodinae
    GET /api/v1/species/?genus=Austroplatypus
    GET /api/v1/species/?tribe=Platypodini
    """
    serializer_class = SpeciesSerializer

    def list(self, request):
        """
        List all species from CSV, optionally filtered by taxonomy.
        Returns all results without pagination.
        """
        # Get filter parameters
        subfamily = request.query_params.get('subfamily')
        genus = request.query_params.get('genus')
        tribe = request.query_params.get('tribe')
        species_param = request.query_params.get('species')

        # Load all species from CSV
        try:
            all_rows = species_ref._load_all_rows()
        except Exception as e:
            return Response({'error': f'Failed to load species reference: {str(e)}'}, status=500)

        # Filter rows based on query params
        filtered_rows = all_rows

        if subfamily:
            filtered_rows = [r for r in filtered_rows if r.get('subfamily', '').lower() == subfamily.lower()]

        if genus:
            filtered_rows = [r for r in filtered_rows if r.get('genus', '').lower() == genus.lower()]

        if tribe:
            filtered_rows = [r for r in filtered_rows if r.get('tribe', '').lower() == tribe.lower()]

        if species_param:
            filtered_rows = [r for r in filtered_rows if r.get('species', '').lower() == species_param.lower()]

        # Serialize and return all results
        serializer = SpeciesSerializer(filtered_rows, many=True)

        return Response(serializer.data)
