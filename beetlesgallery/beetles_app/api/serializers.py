from rest_framework import serializers
from beetlesgallery.beetles_app.models import ImageAsset, Beetles


class ImageAssetSerializer(serializers.ModelSerializer):
    """
    Serializer for ImageAsset model.
    Returns all image metadata and file URLs.
    """
    class Meta:
        model = ImageAsset
        fields = '__all__'


class SpeciesSerializer(serializers.Serializer):
    """
    Serializer for species from valid_species.csv with associated images.
    Returns all taxonomy fields from CSV plus any beetle specimens that depict this species.
    """
    valid_species_id = serializers.CharField()
    scientificName = serializers.CharField()
    scientificNameAuthority = serializers.CharField(allow_blank=True)
    subfamily = serializers.CharField(allow_blank=True)
    tribe = serializers.CharField(allow_blank=True)
    subtribe = serializers.CharField(allow_blank=True)
    genus = serializers.CharField(allow_blank=True)
    species = serializers.CharField(allow_blank=True)
    subspecies = serializers.CharField(allow_blank=True)
    authority = serializers.CharField(allow_blank=True)
    authorityYear = serializers.CharField(allow_blank=True)
    originalGenus = serializers.CharField(allow_blank=True)

    # Images for this species
    images = serializers.SerializerMethodField()

    def get_images(self, obj):
        """
        Get all beetle specimens (with images) that depict this species.
        Returns list of beetles with their associated image assets.
        """
        species_id = obj.get('valid_species_id')
        beetles = Beetles.objects.filter(depicts_valid_name_id=species_id).select_related('image_asset')
        return BeetlesSerializer(beetles, many=True).data


class BeetlesSerializer(serializers.ModelSerializer):
    """
    Serializer for Beetles (specimen) model.
    Includes full nested ImageAsset data plus taxonomy information from reference.
    Now also includes bounding box annotation fields.
    """
    # For reading, return full ImageAsset object
    image_asset = ImageAssetSerializer(read_only=True)
    # For writing, accept image_asset UUID
    image_asset_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    subfamily = serializers.SerializerMethodField()
    genus = serializers.SerializerMethodField()
    tribe = serializers.SerializerMethodField()
    scientific_name = serializers.SerializerMethodField()

    # Bbox user fields with readable usernames
    bbox_created_by_username = serializers.CharField(source='bbox_created_by.username', read_only=True)
    bbox_validated_by_username = serializers.CharField(source='bbox_validated_by.username', read_only=True)

    class Meta:
        model = Beetles
        fields = '__all__'

    def create(self, validated_data):
        """Handle image_asset_id when creating and load the ImageAsset object"""
        image_asset_id = validated_data.pop('image_asset_id', None)
        if image_asset_id:
            # Load the actual ImageAsset instance
            try:
                image_asset = ImageAsset.objects.get(id=image_asset_id)
                validated_data['image_asset'] = image_asset
            except ImageAsset.DoesNotExist:
                raise serializers.ValidationError({'image_asset_id': f'ImageAsset with id {image_asset_id} does not exist'})
        return super().create(validated_data)

    def get_subfamily(self, obj):
        """Look up subfamily natively from Postgres Taxon relationship"""
        return obj.taxon.subfamily if obj.taxon else None

    def get_genus(self, obj):
        """Look up genus natively from Postgres Taxon relationship"""
        return obj.taxon.genus if obj.taxon else None

    def get_tribe(self, obj):
        """Look up tribe natively from Postgres Taxon relationship"""
        return obj.taxon.tribe if obj.taxon else None

    def get_scientific_name(self, obj):
        """Look up scientific name natively from Postgres Taxon relationship"""
        return obj.taxon.scientific_name if obj.taxon else None

    def validate(self, data):
        """
        Validate bounding box coordinates if present.
        Ensures coordinates are within [0, 1] range and box doesn't extend beyond image bounds.
        Allows null values for clearing bbox data.
        """
        bbox_x = data.get('bbox_x')
        bbox_y = data.get('bbox_y')
        bbox_width = data.get('bbox_width')
        bbox_height = data.get('bbox_height')

        errors = {}

        # Only validate if bbox coordinates are provided and not explicitly set to None
        # Check if key exists and value is not None
        if 'bbox_x' in data and bbox_x is not None:
            if not (0 <= bbox_x <= 1):
                errors['bbox_x'] = f"bbox_x must be between 0 and 1 (got {bbox_x})"
        if 'bbox_y' in data and bbox_y is not None:
            if not (0 <= bbox_y <= 1):
                errors['bbox_y'] = f"bbox_y must be between 0 and 1 (got {bbox_y})"
        if 'bbox_width' in data and bbox_width is not None:
            if not (0 < bbox_width <= 1):
                errors['bbox_width'] = f"bbox_width must be between 0 and 1 (got {bbox_width})"
        if 'bbox_height' in data and bbox_height is not None:
            if not (0 < bbox_height <= 1):
                errors['bbox_height'] = f"bbox_height must be between 0 and 1 (got {bbox_height})"

        # Check box doesn't extend beyond bounds (only if values are not None)
        if bbox_x is not None and bbox_width is not None and bbox_x + bbox_width > 1.0001:
            errors['bbox_width'] = f"Box extends beyond right edge (bbox_x + bbox_width = {bbox_x + bbox_width})"
        if bbox_y is not None and bbox_height is not None and bbox_y + bbox_height > 1.0001:
            errors['bbox_height'] = f"Box extends beyond bottom edge (bbox_y + bbox_height = {bbox_y + bbox_height})"

        if errors:
            raise serializers.ValidationError(errors)

        return data
