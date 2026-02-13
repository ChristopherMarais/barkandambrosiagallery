from rest_framework import serializers
from beetlesgallery.beetles_app.models import ImageAsset, Beetles, BoundingBox
from beetlesgallery.beetles_app import species_ref


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
    """
    image_asset = ImageAssetSerializer(read_only=True)
    subfamily = serializers.SerializerMethodField()
    genus = serializers.SerializerMethodField()
    tribe = serializers.SerializerMethodField()
    scientific_name = serializers.SerializerMethodField()

    class Meta:
        model = Beetles
        fields = '__all__'

    def get_subfamily(self, obj):
        """Look up subfamily from valid_species reference"""
        if not obj.depicts_valid_name_id:
            return None
        try:
            species_data = species_ref.lookup(obj.depicts_valid_name_id)
            return species_data.get('subfamily') if species_data else None
        except:
            return None

    def get_genus(self, obj):
        """Look up genus from valid_species reference"""
        if not obj.depicts_valid_name_id:
            return None
        try:
            species_data = species_ref.lookup(obj.depicts_valid_name_id)
            return species_data.get('genus') if species_data else None
        except:
            return None

    def get_tribe(self, obj):
        """Look up tribe from valid_species reference"""
        if not obj.depicts_valid_name_id:
            return None
        try:
            species_data = species_ref.lookup(obj.depicts_valid_name_id)
            return species_data.get('tribe') if species_data else None
        except:
            return None

    def get_scientific_name(self, obj):
        """Look up scientific name from valid_species reference"""
        if not obj.depicts_valid_name_id:
            return None
        try:
            species_data = species_ref.lookup(obj.depicts_valid_name_id)
            return species_data.get('scientificName') if species_data else None
        except:
            return None


class BoundingBoxSerializer(serializers.ModelSerializer):
    """
    Serializer for BoundingBox model.
    Handles CRUD operations for bounding box annotations.
    """
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    validated_by_username = serializers.CharField(source='validated_by.username', read_only=True)

    class Meta:
        model = BoundingBox
        fields = [
            'id', 'image_asset', 'beetle', 'x', 'y', 'width', 'height',
            'label', 'confidence', 'source', 'created_by', 'created_by_username',
            'created_at', 'updated_at', 'is_validated', 'validated_by',
            'validated_by_username', 'validated_at', 'notes'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by_username', 'validated_by_username']

    def validate(self, data):
        """
        Validate bounding box coordinates.
        Ensures coordinates are within [0, 1] range and box doesn't extend beyond image bounds.
        """
        x = data.get('x')
        y = data.get('y')
        width = data.get('width')
        height = data.get('height')

        errors = {}

        if x is not None and not (0 <= x <= 1):
            errors['x'] = f"x must be between 0 and 1 (got {x})"
        if y is not None and not (0 <= y <= 1):
            errors['y'] = f"y must be between 0 and 1 (got {y})"
        if width is not None and not (0 < width <= 1):
            errors['width'] = f"width must be between 0 and 1 (got {width})"
        if height is not None and not (0 < height <= 1):
            errors['height'] = f"height must be between 0 and 1 (got {height})"

        # Check box doesn't extend beyond bounds
        if x is not None and width is not None and x + width > 1.0001:
            errors['width'] = f"Box extends beyond right edge (x + width = {x + width})"
        if y is not None and height is not None and y + height > 1.0001:
            errors['height'] = f"Box extends beyond bottom edge (y + height = {y + height})"

        if errors:
            raise serializers.ValidationError(errors)

        return data
