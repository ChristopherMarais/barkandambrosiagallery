from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import LimitOffsetPagination
from django.http import FileResponse
from beetlesgallery.beetles_app.models import ImageAsset, Beetles
from beetlesgallery.beetles_app import species_ref
from .serializers import ImageAssetSerializer, BeetlesSerializer, SpeciesSerializer


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
