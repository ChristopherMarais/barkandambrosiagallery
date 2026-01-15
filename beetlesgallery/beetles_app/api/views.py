from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse
from beetlesgallery.beetles_app.models import ImageAsset, Beetles
from beetlesgallery.beetles_app import species_ref
from .serializers import ImageAssetSerializer, BeetlesSerializer


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
                    # No matches - return empty queryset
                    queryset = queryset.none()
            except Exception:
                # If lookup fails, return empty queryset
                queryset = queryset.none()

        return queryset
