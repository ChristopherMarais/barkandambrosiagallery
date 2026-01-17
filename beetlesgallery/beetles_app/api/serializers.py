from rest_framework import serializers
from beetlesgallery.beetles_app.models import ImageAsset, Beetles
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
