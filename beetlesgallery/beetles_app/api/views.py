from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.http import FileResponse
from django.db import transaction
from beetlesgallery.beetles_app.models import ImageAsset, Beetles, BoundingBox
from beetlesgallery.beetles_app import species_ref
from .serializers import ImageAssetSerializer, BeetlesSerializer, SpeciesSerializer, BoundingBoxSerializer
import json
import zipfile
import os
from io import BytesIO


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


class BeetlesViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for Beetles (specimen) records.

    Supports filtering by subfamily via query parameter:
    GET /api/v1/beetles/?subfamily=Platypodinae
    """
    serializer_class = BeetlesSerializer

    def get_queryset(self):
        """
        Filter beetles by subfamily if provided in query params.
        Uses the valid_species.csv reference to look up which species IDs belong to the subfamily.
        """
        queryset = Beetles.objects.select_related('image_asset').all()

        # Filter by subfamily if provided
        subfamily = self.request.query_params.get('subfamily')
        if subfamily:
            # Look up all valid_species_ids that match this subfamily
            try:
                matching_ids = species_ref.ids_for('subfamily', subfamily)
                if matching_ids:
                    # Filter beetles by these IDs
                    queryset = queryset.filter(depicts_valid_name_id__in=matching_ids)
                else:
                    queryset = queryset.none()
            except Exception:
                queryset = queryset.none()

        return queryset


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


class BoundingBoxViewSet(viewsets.ModelViewSet):
    """
    API endpoint for BoundingBox annotations.

    Supports:
    - CRUD operations on individual bounding boxes
    - Bulk import from YOLO/COCO format via /import-annotations/
    - Export annotations via /export/

    Staff-only access required.
    """
    queryset = BoundingBox.objects.all()
    serializer_class = BoundingBoxSerializer
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        """
        Filter bounding boxes by image_asset or beetle if provided.
        """
        queryset = BoundingBox.objects.select_related('image_asset', 'beetle', 'created_by', 'validated_by').all()

        image_asset_id = self.request.query_params.get('image_asset')
        beetle_id = self.request.query_params.get('beetle')

        if image_asset_id:
            queryset = queryset.filter(image_asset_id=image_asset_id)
        if beetle_id:
            queryset = queryset.filter(beetle_id=beetle_id)

        return queryset

    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['post'], url_path='import-annotations')
    def import_annotations(self, request):
        """
        Bulk import annotations from YOLO or COCO format.

        Expects a ZIP file containing annotation files where each filename
        (without extension) matches a Beetle UUID.

        POST /api/v1/bounding-boxes/import-annotations/

        Form data:
        - file: ZIP file containing annotation files
        - format: 'yolo' or 'coco'
        - source: 'manual', 'ai', or 'imported' (default: 'imported')
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
        source = request.data.get('source', 'imported')
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
                    zf, annotation_format, source, overwrite, request.user
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

    def _process_annotation_zip(self, zip_file, annotation_format, source, overwrite, user):
        """
        Process annotation files from ZIP and create BoundingBox records.

        Returns statistics about the import.
        """
        imported_count = 0
        skipped_count = 0
        errors = []
        details = {}

        # Get list of annotation files
        file_list = [f for f in zip_file.namelist() if not f.startswith('__MACOSX') and not f.endswith('/')]

        # Special handling for COCO format with single annotations.json file
        if annotation_format == 'coco' and 'annotations.json' in file_list:
            return self._process_coco_all_annotations(zip_file, source, overwrite, user)

        with transaction.atomic():
            for filename in file_list:
                try:
                    # Extract beetle UUID from filename (remove extension)
                    beetle_uuid = os.path.splitext(os.path.basename(filename))[0]

                    # Look up the Beetle record
                    try:
                        beetle = Beetles.objects.select_related('image_asset').get(pk=beetle_uuid)
                    except Beetles.DoesNotExist:
                        skipped_count += 1
                        errors.append(f'{filename}: No beetle found with UUID {beetle_uuid}')
                        continue

                    if not beetle.image_asset:
                        skipped_count += 1
                        errors.append(f'{filename}: Beetle {beetle_uuid} has no associated image')
                        continue

                    image_asset = beetle.image_asset

                    # Check if annotations already exist
                    existing_boxes_count = BoundingBox.objects.filter(image_asset=image_asset).count()

                    if existing_boxes_count > 0 and not overwrite:
                        skipped_count += 1
                        errors.append(
                            f'{filename}: Image already has {existing_boxes_count} annotation(s). '
                            f'Enable "Overwrite existing annotations" to replace them.'
                        )
                        continue

                    # Read annotation file content
                    content = zip_file.read(filename).decode('utf-8')

                    # Parse annotations based on format
                    if annotation_format == 'yolo':
                        boxes = self._parse_yolo_annotations(content, image_asset, beetle, source, user)
                    else:  # coco
                        boxes = self._parse_coco_annotations(content, image_asset, beetle, source, user)

                    # Delete existing annotations if overwrite is True
                    if overwrite and existing_boxes_count > 0:
                        BoundingBox.objects.filter(image_asset=image_asset).delete()

                    # Bulk create bounding boxes
                    if boxes:
                        BoundingBox.objects.bulk_create(boxes)
                        imported_count += len(boxes)
                        details[beetle_uuid] = {
                            'filename': filename,
                            'boxes_imported': len(boxes),
                            'replaced': existing_boxes_count if overwrite else 0
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

    def _process_coco_all_annotations(self, zip_file, source, overwrite, user):
        """
        Process a single COCO annotations.json file containing all images and annotations.

        This is used when importing COCO format exports that contain a single JSON file
        with multiple images and their annotations.
        """
        imported_count = 0
        skipped_count = 0
        errors = []
        details = {}

        try:
            # Read and parse the annotations.json file
            content = zip_file.read('annotations.json').decode('utf-8')
            data = json.loads(content)

            images = data.get('images', [])
            annotations = data.get('annotations', [])

            # Build a map of image_id to annotations
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

                        # Extract beetle UUID from file_name (e.g., "uuid.jpg" -> "uuid")
                        beetle_uuid = os.path.splitext(file_name)[0]

                        # Look up the Beetle record
                        try:
                            beetle = Beetles.objects.select_related('image_asset').get(pk=beetle_uuid)
                        except Beetles.DoesNotExist:
                            skipped_count += 1
                            errors.append(f'{file_name}: No beetle found with UUID {beetle_uuid}')
                            continue

                        if not beetle.image_asset:
                            skipped_count += 1
                            errors.append(f'{file_name}: Beetle {beetle_uuid} has no associated image')
                            continue

                        image_asset = beetle.image_asset

                        # Check if annotations already exist
                        existing_boxes_count = BoundingBox.objects.filter(image_asset=image_asset).count()

                        if existing_boxes_count > 0 and not overwrite:
                            skipped_count += 1
                            errors.append(
                                f'{file_name}: Image already has {existing_boxes_count} annotation(s). '
                                f'Enable "Overwrite existing annotations" to replace them.'
                            )
                            continue

                        # Get annotations for this image
                        image_annotations = annotations_by_image.get(image_id, [])

                        # Parse bounding boxes
                        boxes = []
                        for ann in image_annotations:
                            bbox = ann.get('bbox')
                            if not bbox or len(bbox) < 4:
                                continue

                            # COCO bbox format: [x, y, width, height] in pixels
                            x_px = bbox[0]
                            y_px = bbox[1]
                            w_px = bbox[2]
                            h_px = bbox[3]

                            # Use image dimensions from COCO or fallback to ImageAsset
                            if img_width and img_height:
                                width_for_norm = img_width
                                height_for_norm = img_height
                            elif image_asset.image_width and image_asset.image_height:
                                width_for_norm = image_asset.image_width
                                height_for_norm = image_asset.image_height
                            else:
                                continue  # Skip if no dimensions available

                            # Convert to normalized coordinates
                            x = x_px / width_for_norm
                            y = y_px / height_for_norm
                            width = w_px / width_for_norm
                            height = h_px / height_for_norm

                            # Get label and confidence
                            label = str(ann.get('category_id', ''))
                            confidence = ann.get('score')

                            # Create BoundingBox instance
                            box = BoundingBox(
                                image_asset=image_asset,
                                beetle=beetle,
                                x=x,
                                y=y,
                                width=width,
                                height=height,
                                label=label,
                                confidence=confidence,
                                source=source,
                                created_by=user
                            )

                            # Validate coordinates
                            if 0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1:
                                if x + width <= 1.0001 and y + height <= 1.0001:
                                    boxes.append(box)

                        # Delete existing annotations if overwrite is True
                        if overwrite and existing_boxes_count > 0:
                            BoundingBox.objects.filter(image_asset=image_asset).delete()

                        # Bulk create bounding boxes
                        if boxes:
                            BoundingBox.objects.bulk_create(boxes)
                            imported_count += len(boxes)
                            details[beetle_uuid] = {
                                'filename': file_name,
                                'boxes_imported': len(boxes),
                                'replaced': existing_boxes_count if overwrite else 0
                            }
                        else:
                            skipped_count += 1
                            errors.append(f'{file_name}: No valid bounding boxes found')

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

    def _parse_yolo_annotations(self, content, image_asset, beetle, source, user):
        """
        Parse YOLO format annotations.

        YOLO format: <class_id> <x_center> <y_center> <width> <height>
        All coordinates are normalized (0-1).
        """
        boxes = []

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

                # Create BoundingBox instance
                box = BoundingBox(
                    image_asset=image_asset,
                    beetle=beetle,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    label=label,
                    source=source,
                    created_by=user
                )

                # Validate coordinates
                if 0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1:
                    if x + width <= 1.0001 and y + height <= 1.0001:
                        boxes.append(box)

            except (ValueError, IndexError):
                continue

        return boxes

    def _parse_coco_annotations(self, content, image_asset, beetle, source, user):
        """
        Parse COCO format annotations.

        COCO format is JSON with structure:
        {
            "images": [{"id": 1, "width": 1920, "height": 1080, ...}],
            "annotations": [
                {
                    "image_id": 1,
                    "bbox": [x, y, width, height],  // in pixels
                    "category_id": "beetle",
                    ...
                }
            ]
        }
        """
        boxes = []

        try:
            data = json.loads(content)

            # Find image info
            image_info = None
            if 'images' in data and len(data['images']) > 0:
                image_info = data['images'][0]

            # Get image dimensions (from COCO or from ImageAsset)
            if image_info and 'width' in image_info and 'height' in image_info:
                img_width = image_info['width']
                img_height = image_info['height']
            elif image_asset.image_width and image_asset.image_height:
                img_width = image_asset.image_width
                img_height = image_asset.image_height
            else:
                # Cannot parse without dimensions
                return boxes

            # Parse annotations
            annotations = data.get('annotations', [])

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

                # Get label
                label = str(ann.get('category_id', ''))
                confidence = ann.get('score')

                # Create BoundingBox instance
                box = BoundingBox(
                    image_asset=image_asset,
                    beetle=beetle,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    label=label,
                    confidence=confidence,
                    source=source,
                    created_by=user
                )

                # Validate coordinates
                if 0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1:
                    if x + width <= 1.0001 and y + height <= 1.0001:
                        boxes.append(box)

        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        return boxes

    @action(detail=False, methods=['get'], url_path='images-with-annotations')
    def images_with_annotations(self, request):
        """
        Get list of all images that have bounding box annotations.

        GET /api/v1/bounding-boxes/images-with-annotations/

        Returns:
        {
            "images": [
                {
                    "image_asset_id": "uuid",
                    "beetle_id": "uuid",
                    "beetle_alternative_id": "alternative_id",
                    "filename": "image.jpg",
                    "thumbnail_url": "/media/...",
                    "annotation_count": 5,
                    "created_at": "2024-01-01T00:00:00Z"
                },
                ...
            ]
        }
        """
        from django.db.models import Count

        # Get all image assets that have bounding boxes
        images_with_boxes = BoundingBox.objects.values('image_asset_id').annotate(
            box_count=Count('id')
        ).filter(box_count__gt=0)

        image_asset_ids = [item['image_asset_id'] for item in images_with_boxes]

        # Fetch image asset details with related beetle info
        images = []
        for image_asset_id in image_asset_ids:
            try:
                image_asset = ImageAsset.objects.get(pk=image_asset_id)
                beetle = image_asset.specimens.first()  # Get first beetle for this image

                if not beetle:
                    continue

                # Get annotation count
                box_count = BoundingBox.objects.filter(image_asset=image_asset).count()

                # Check if any bounding boxes are unvalidated
                has_unvalidated_boxes = BoundingBox.objects.filter(
                    image_asset=image_asset,
                    is_validated=False
                ).exists()

                images.append({
                    'image_asset_id': str(image_asset.id),
                    'beetle_id': str(beetle.id),
                    'filename': os.path.basename(image_asset.image_file.name) if image_asset.image_file else 'unknown',
                    'thumbnail_url': image_asset.thumb_small.url if image_asset.thumb_small else None,
                    'full_image_url': image_asset.display_url,
                    'annotation_count': box_count,
                    'has_unvalidated_boxes': has_unvalidated_boxes,
                    'created_at': beetle.last_updated_at.isoformat() if beetle.last_updated_at else None,
                })
            except ImageAsset.DoesNotExist:
                continue

        # Sort by created_at descending (most recent first)
        images.sort(key=lambda x: x['created_at'] or '', reverse=True)

        return Response({'images': images})

    @action(detail=False, methods=['get'], url_path='export-annotations')
    def export_annotations(self, request):
        """
        Export annotations for all or selected images in YOLO or COCO format.

        GET /api/v1/bounding-boxes/export-annotations/?format=yolo
        GET /api/v1/bounding-boxes/export-annotations/?format=coco&image_asset=<uuid>

        Query params:
        - format: 'yolo' or 'coco' (default: 'yolo')
        - image_asset: Optional - One or more image asset UUIDs. If not provided, exports all.

        Returns a ZIP file containing annotation files.
        """
        export_format = request.query_params.get('format', 'yolo').lower()
        image_asset_ids = request.query_params.getlist('image_asset')

        # If no specific IDs provided, get all images with annotations
        if not image_asset_ids:
            image_asset_ids = BoundingBox.objects.values_list('image_asset_id', flat=True).distinct()

        if export_format not in ['yolo', 'coco']:
            return Response(
                {'error': 'Invalid format. Must be "yolo" or "coco".'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            if export_format == 'coco':
                # COCO: Export all annotations in a single JSON file
                content = self._export_all_coco(image_asset_ids)

                # Create ZIP with single JSON file
                zip_buffer = BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr('annotations.json', content)

                zip_buffer.seek(0)
                response = FileResponse(
                    zip_buffer,
                    content_type='application/zip',
                    as_attachment=True,
                    filename='annotations_coco.zip'
                )
                return response
            else:
                # YOLO: Export separate .txt file per image
                zip_buffer = BytesIO()

                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for image_id in image_asset_ids:
                        try:
                            image_asset = ImageAsset.objects.get(pk=image_id)
                            boxes = BoundingBox.objects.filter(image_asset=image_asset)

                            if not boxes.exists():
                                continue

                            # Get primary beetle for filename
                            beetle = image_asset.specimens.first()
                            if not beetle:
                                continue

                            filename = f"{beetle.id}.txt"
                            content = self._export_yolo(boxes)
                            zf.writestr(filename, content)

                        except ImageAsset.DoesNotExist:
                            continue

                zip_buffer.seek(0)

                response = FileResponse(
                    zip_buffer,
                    content_type='application/zip',
                    as_attachment=True,
                    filename='annotations_yolo.zip'
                )

                return response

        except Exception as e:
            return Response(
                {'error': f'Export failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _export_yolo(self, boxes):
        """Export bounding boxes to YOLO format string."""
        lines = []
        for box in boxes:
            line = box.to_yolo()
            lines.append(line)
        return '\n'.join(lines)

    def _export_all_coco(self, image_asset_ids):
        """Export all bounding boxes to a single COCO format JSON string."""
        data = {
            'images': [],
            'annotations': [],
            'categories': []
        }

        annotation_id = 1

        for image_id in image_asset_ids:
            try:
                image_asset = ImageAsset.objects.get(pk=image_id)
                boxes = BoundingBox.objects.filter(image_asset=image_asset)

                if not boxes.exists():
                    continue

                # Add image info
                data['images'].append({
                    'id': str(image_asset.id),
                    'width': image_asset.image_width or 0,
                    'height': image_asset.image_height or 0,
                    'file_name': os.path.basename(image_asset.image_file.name) if image_asset.image_file else ''
                })

                # Add annotations for this image
                for box in boxes:
                    coco_ann = box.to_coco(
                        image_asset.image_width or 1920,
                        image_asset.image_height or 1080
                    )
                    coco_ann['id'] = annotation_id
                    coco_ann['image_id'] = str(image_asset.id)
                    data['annotations'].append(coco_ann)
                    annotation_id += 1

            except ImageAsset.DoesNotExist:
                continue

        return json.dumps(data, indent=2)

    def _export_coco(self, boxes, image_asset):
        """Export bounding boxes to COCO format JSON string."""
        data = {
            'images': [{
                'id': str(image_asset.id),
                'width': image_asset.image_width or 0,
                'height': image_asset.image_height or 0,
                'file_name': os.path.basename(image_asset.image_file.name) if image_asset.image_file else ''
            }],
            'annotations': []
        }

        for idx, box in enumerate(boxes):
            coco_ann = box.to_coco(
                image_asset.image_width or 1920,
                image_asset.image_height or 1080
            )
            coco_ann['id'] = idx + 1
            coco_ann['image_id'] = str(image_asset.id)
            data['annotations'].append(coco_ann)

        return json.dumps(data, indent=2)


@api_view(['GET'])
@permission_classes([IsStaffUser])
def export_annotations_view(request):
    """
    Standalone view to export all annotations in YOLO or COCO format.

    GET /api/v1/bounding-boxes/export-annotations/?format=yolo
    GET /api/v1/bounding-boxes/export-annotations/?format=coco
    """
    export_format = request.query_params.get('format', 'yolo').lower()

    if export_format not in ['yolo', 'coco']:
        return Response(
            {'error': 'Invalid format. Must be "yolo" or "coco".'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Get all images with annotations
    image_asset_ids = BoundingBox.objects.values_list('image_asset_id', flat=True).distinct()

    try:
        if export_format == 'coco':
            # COCO: Export all annotations in a single JSON file
            data = {
                'images': [],
                'annotations': [],
                'categories': []
            }

            annotation_id = 1

            for image_id in image_asset_ids:
                try:
                    image_asset = ImageAsset.objects.get(pk=image_id)
                    boxes = BoundingBox.objects.filter(image_asset=image_asset)

                    if not boxes.exists():
                        continue

                    # Add image info
                    data['images'].append({
                        'id': str(image_asset.id),
                        'width': image_asset.image_width or 0,
                        'height': image_asset.image_height or 0,
                        'file_name': os.path.basename(image_asset.image_file.name) if image_asset.image_file else ''
                    })

                    # Add annotations for this image
                    for box in boxes:
                        coco_ann = box.to_coco(
                            image_asset.image_width or 1920,
                            image_asset.image_height or 1080
                        )
                        coco_ann['id'] = annotation_id
                        coco_ann['image_id'] = str(image_asset.id)
                        data['annotations'].append(coco_ann)
                        annotation_id += 1

                except ImageAsset.DoesNotExist:
                    continue

            content = json.dumps(data, indent=2)

            # Create ZIP with single JSON file
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('annotations.json', content)

            zip_buffer.seek(0)
            response = FileResponse(
                zip_buffer,
                content_type='application/zip',
                as_attachment=True,
                filename='annotations_coco.zip'
            )
            return response

        else:
            # YOLO: Export separate .txt file per image
            zip_buffer = BytesIO()

            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for image_id in image_asset_ids:
                    try:
                        image_asset = ImageAsset.objects.get(pk=image_id)
                        boxes = BoundingBox.objects.filter(image_asset=image_asset)

                        if not boxes.exists():
                            continue

                        # Get primary beetle for filename
                        beetle = image_asset.specimens.first()
                        if not beetle:
                            continue

                        filename = f"{beetle.id}.txt"

                        # Export YOLO format
                        lines = []
                        for box in boxes:
                            line = box.to_yolo()
                            lines.append(line)
                        content = '\n'.join(lines)

                        zf.writestr(filename, content)

                    except ImageAsset.DoesNotExist:
                        continue

            zip_buffer.seek(0)

            response = FileResponse(
                zip_buffer,
                content_type='application/zip',
                as_attachment=True,
                filename='annotations_yolo.zip'
            )

            return response

    except Exception as e:
        return Response(
            {'error': f'Export failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
