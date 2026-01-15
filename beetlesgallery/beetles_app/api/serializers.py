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
