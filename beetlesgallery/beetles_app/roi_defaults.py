"""
What a new ROI starts with. One place, so a box drawn with the mouse and a box proposed by the classifier agree.

An image can hold a "template" ROI: the image's metadata with no box yet. The first new box fills it in.
Otherwise a new ROI copies the metadata of the image's most recently updated ROI.
"""
from .models import Beetles

# The species the ROI is labelled as (a box proposed by the classifier brings its own)
SPECIES_FIELDS = ("taxon", "depicts_valid_name_id", "depicts_described_name_id", "depicts_name_verbatim")
# Everything else that describes the specimen and how it was collected
SPECIMEN_FIELDS = (
    "aspect", "depicts_specimen", "alias_id", "collection_country", "collection_stateProvince",
    "specimen_sex", "specimen_type_status", "specimen_notes",
)


def template_roi(asset):
    """The image's box-less ROI, or None."""
    return Beetles.objects.filter(image_asset=asset, bbox_x__isnull=True, is_deleted=False).first()


def latest_roi(asset):
    return Beetles.objects.filter(image_asset=asset, is_deleted=False).order_by("-last_updated_at").first()


def inherited_fields(roi, species=True):
    """The metadata a new ROI copies from ``roi``; {} when there is none."""
    if roi is None:
        return {}
    names = (SPECIES_FIELDS if species else ()) + SPECIMEN_FIELDS
    return {name: getattr(roi, name) for name in names}
