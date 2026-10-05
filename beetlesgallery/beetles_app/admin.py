from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils import timezone
from .models import (
    UploadBatch, UpdateBatch, Beetles, ImageAsset, DownloadJob, ImageLock,
    Taxon, Synonym, CategoryMapping, GameRound, GameAnswer,
    PlayerSkill, SpeciesDiscovery, RetroCredit, AnswerPoints, PlayerScore, GamePreference, LabelReview, RoiDifficulty, GameReport, ModelPrediction, PathogenInteraction, AccessRequest, InteractionProposal, AreaGrant, SiteNotice, GameTuning, PredictionUpload, RoiName,
)
from simple_history.admin import SimpleHistoryAdmin

@admin.action(description="Restore selected items (Undo Soft Delete)")
def restore_items(modeladmin, request, queryset):
    # 1. Capture the IDs BEFORE we modify the database so the queryset doesn't empty out
    item_ids = list(queryset.values_list('id', flat=True))
    
    # 2. Restore the selected items
    updated_count = queryset.update(is_deleted=False, deleted_at=None)
    
    # 3. Restore dependencies using the safely captured IDs
    if modeladmin.model.__name__ == 'ImageAsset':
        from .models import Beetles
        Beetles.objects.filter(image_asset_id__in=item_ids).update(is_deleted=False, deleted_at=None)
        
    elif modeladmin.model.__name__ == 'Beetles':
        from .models import ImageAsset, Beetles
        image_ids = Beetles.objects.filter(id__in=item_ids).values_list('image_asset_id', flat=True).distinct()
        ImageAsset.objects.filter(id__in=image_ids).update(is_deleted=False, deleted_at=None)
        
    modeladmin.message_user(request, f"Successfully restored {updated_count} items and their dependencies.")


# ---------- DownloadJob ----------
@admin.register(DownloadJob)
class DownloadJobAdmin(admin.ModelAdmin):
    list_display = (
        "id_short", "created_at", "requested_by", "selection_mode", "status",
        "total_requested", "include_images", "started_at", "finished_at", "expires_at"
    )
    list_filter = ("status", "selection_mode", "include_images", "created_at")
    search_fields = ("id", "query_string", "selected_ids_json", "requested_by__username", "requested_by__email")
    readonly_fields = (
        "id", "created_at", "started_at", "finished_at", "expires_at",
        "selection_mode", "query_string", "selected_ids_json",
        "total_requested", "include_images", "status", "error_message",
        "csv_file", "zip_file", "requested_by",
    )

    @admin.display(description='Job ID', ordering='id')
    def id_short(self, obj):
        return str(obj.id)[:8]


# ---------- UploadBatch ----------
@admin.register(UploadBatch)
class UploadBatchAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_select_related = ("uploaded_by",)
    list_per_page = 50
    empty_value_display = "—"

    # columns in admin table view
    list_display = (
        "id_short",
        "status_badge",
        "created_at",
        "updated_at",
        "original_filename",
        "size_readable",
        "uploaded_by",
        "short_sha",
        "file_link",
        "zip_file_link",
        "validated_at",
        "imported_at",
        "error_excerpt",
    )
    # right-hand sidebar filters
    list_filter = ("status", "created_at", "uploaded_by")

    # can use LIKE queries against these fields in search box
    search_fields = (
        "=id",
        "original_filename",
        "sha256"
        )

    # default sort order for the table view
    ordering = ("-created_at",)

    # read only in detail view - ALL FIELDS
    readonly_fields = (
        "id",
        "uploaded_by",
        "file",
        "zip_file",
        "original_filename",
        "size_bytes",
        "sha256",
        "status",
        "error_message",
        "error_report_file",
        "created_at",
        "updated_at",
        "validated_at",
        "imported_at",
    )

    # hides the admin’s bulk actions dropdown
    actions = None


    # ---- Column renderers ----
    # helper methods that return a rendered value for use in list_display

    @admin.display(description='Batch ID', ordering='id')
    def id_short(self, obj):
        return str(obj.id)[:8]

    @admin.display(description="SHA-256")
    def short_sha(self, obj):
        return (obj.sha256 or "")[:12]

    @admin.display(description="Size", ordering="size_bytes")
    def size_readable(self, obj):
        try:
            from django.contrib.humanize.templatetags.humanize import naturalsize
            return naturalsize(obj.size_bytes or 0, binary=True)
        except Exception:
            s = float(obj.size_bytes or 0)
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if s < 1024 or unit == "TB":
                    return f"{s:.1f} {unit}"
                s /= 1024.0

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colors = {
            "staging": "#64748b",       # slate
            "validating": "#eab308",    # amber
            "validated": "#16a34a",     # green
            "rejected": "#dc2626",      # red
            "imported": "#0ea5e9",      # sky
            "import_failed": "#7c3aed", # violet
        }
        bg = colors.get(obj.status, "#475569")
        return format_html(
            '<span style="padding:2px 8px; border-radius:12px; color:#fff; background:{}; font-size:12px;">{}</span>',
            bg, obj.get_status_display()
        )

    @admin.display(description="CSV File")
    def file_link(self, obj):
        if not obj.file:
            return "—"
        try:
            return format_html('<a href="{}" target="_blank">open</a>', obj.file.url)
        except Exception:
            return format_html('<code>{}</code>', obj.file.name)

    @admin.display(description="ZIP File")
    def zip_file_link(self, obj):
        if not obj.zip_file:
            return "—"
        try:
            return format_html('<a href="{}" target="_blank">open</a>', obj.zip_file.url)
        except Exception:
            return format_html('<code>{}</code>', obj.zip_file.name)

    @admin.display(description="Error", ordering="error_message")
    def error_excerpt(self, obj):
        if not obj.error_message:
            return "—"
        msg = obj.error_message.strip()
        return (msg[:90] + "…") if len(msg) > 90 else msg

    # Lock down admin editing
    def has_view_permission(self, request, obj=None): return True
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False  # remove this to allow reverting changes in admin
    def has_delete_permission(self, request, obj=None): return False


# ---------- Beetles ----------
@admin.register(Beetles)
class BeetlesAdmin(SimpleHistoryAdmin):
    # Use double-underscore to access related date field
    date_hierarchy = "image_asset__image_date_taken" 
    list_per_page = 50
    empty_value_display = "—"

    # ---> NEW: Register the action <---
    actions = [restore_items]

    # LIST DISPLAY: ALL key fields
    list_display = (
        "id_short",
        "image_preview_in_beetle",
        "depicts_valid_name_id",
        "depicts_described_name_id",
        "depicts_name_verbatim",
        "depicts_specimen",
        "alias_id",
        "aspect",
        "get_photographer",
        "get_institution",
        "collection_country",
        "collection_stateProvince",
        "specimen_sex",
        "specimen_type_status",
        "has_bbox_annotation",
        "bbox_is_validated",
        "is_deleted",
        "last_updated_at",
    )

    # LIST FILTERS: Use double-underscore to filter across the relationship
    list_filter = (
        "is_deleted", # ---> NEW <---
        "collection_country",
        "collection_stateProvince",
        "image_asset__photographer",
        "image_asset__image_institution",
        "image_asset__image_has_multiple_individuals",
        "specimen_sex",
        "specimen_type_status",
        "bbox_is_validated",
        "bbox_created_by",
    )

    # SEARCH FIELDS: Use double-underscore for related search
    search_fields = (
        "=id", 
        "depicts_valid_name_id",
        "alias_id",
        "depicts_described_name_id",
        "depicts_name_verbatim",
        "image_asset__photographer",
        "image_asset__image_institution",
        "collection_country",
        "collection_stateProvince",
        "specimen_notes",
        "image_asset__image_notes",
    )

    ordering = ("depicts_valid_name_id", "-image_asset__image_date_taken")

    # READONLY FIELDS: ALL fields from both Beetles and ImageAsset
    readonly_fields = (
        "id",
        "image_asset",
        "taxon",
        "image_preview_in_beetle",
        "get_full_path",
        "alias_id",
        "aspect",
        "get_institution",
        "get_photographer",
        "get_email",
        "get_usage_statement",
        "get_resolution",
        "get_image_notes",
        "get_date_taken",
        "get_multiple_individuals",
        "depicts_specimen",
        "depicts_valid_name_id",
        "depicts_described_name_id",
        "depicts_name_verbatim",
        "collection_country",
        "collection_stateProvince",
        "specimen_sex",
        "specimen_type_status",
        "specimen_notes",
        "last_updated_by",
        "last_updated_at",
        "update_notes",
        "has_bbox_annotation",
        "bbox_x",
        "bbox_y",
        "bbox_width",
        "bbox_height",
        "bbox_is_validated",
        "bbox_validated_by",
        "bbox_validated_at",
        "bbox_created_by",
        "bbox_created_at",
        "is_deleted",
        "deleted_at",
    )

    # ---> UPDATED: Allow both delete AND restore actions <---
    def get_actions(self, request):
        actions = super().get_actions(request)
        allowed_actions = {}
        if 'delete_selected' in actions:
            allowed_actions['delete_selected'] = actions['delete_selected']
        if 'restore_items' in actions:
            allowed_actions['restore_items'] = actions['restore_items']
        return allowed_actions

    @admin.display(description='Has BBox', boolean=True, ordering='bbox_x')
    def has_bbox_annotation(self, obj):
        """Show whether this beetle has bounding box data"""
        return obj.has_bbox()

    @admin.display(description='ID', ordering='id')
    def id_short(self, obj):
        return str(obj.id)[:8]

    # --- Accessors for ImageAsset Data ---
    # These functions allow the Admin to read data from the linked table
    
    @admin.display(description='Image Preview')
    def image_preview_in_beetle(self, obj):
        if obj.image_asset and obj.image_asset.image_file:
            return mark_safe(f'<img src="{obj.image_asset.image_file.url}" style="max-height: 200px;"/>')
        return "-"

    @admin.display(description='Photographer', ordering='image_asset__photographer')
    def get_photographer(self, obj):
        return obj.image_asset.photographer if obj.image_asset else "-"

    @admin.display(description='Institution', ordering='image_asset__image_institution')
    def get_institution(self, obj):
        return obj.image_asset.image_institution if obj.image_asset else "-"

    @admin.display(description='Date Taken', ordering='image_asset__image_date_taken')
    def get_date_taken(self, obj):
        return obj.image_asset.image_date_taken if obj.image_asset else "-"

    @admin.display(description='Full Path')
    def get_full_path(self, obj):
        return obj.image_asset.full_path_at_import if obj.image_asset else "-"

    @admin.display(description='Email')
    def get_email(self, obj):
        return obj.image_asset.image_email if obj.image_asset else "-"

    @admin.display(description='Usage Statement')
    def get_usage_statement(self, obj):
        return obj.image_asset.photo_usage_statement if obj.image_asset else "-"

    @admin.display(description='Resolution')
    def get_resolution(self, obj):
        return obj.image_asset.resolution_in_ppmm if obj.image_asset else "-"

    @admin.display(description='Image Notes')
    def get_image_notes(self, obj):
        return obj.image_asset.image_notes if obj.image_asset else "-"

    @admin.display(description='Multiple Individuals?')
    def get_multiple_individuals(self, obj):
        return obj.image_asset.image_has_multiple_individuals if obj.image_asset else "-"

    # Permissions
    def has_view_permission(self, request, obj=None): return True
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser

@admin.register(ImageAsset)
class ImageAssetAdmin(admin.ModelAdmin):
    actions = [restore_items]
    date_hierarchy = "image_date_taken"
    list_per_page = 50
    empty_value_display = "—"

    # ALL FIELDS in list display
    list_display = (
        'id_short',
        'image_preview',
        'short_sha',
        'image_institution',
        'photographer',
        'image_email',
        'image_date_taken',
        'resolution_in_ppmm',
        'image_has_multiple_individuals',
        'size_readable',
        'dimensions',
        'is_validated',
        'is_deleted',
        'last_updated_by',
        'created_at',
        'updated_at',
    )

    search_fields = ('=id', 'image_sha256', 'full_path_at_import', 'image_institution', 'photographer', 'image_email')

    # ALL FIELDS as readonly
    readonly_fields = (
        'id',
        'image_sha256',
        'full_path_at_import',
        'image_institution',
        'photographer',
        'image_email',
        'photo_usage_statement',
        'image_date_taken',
        'image_notes',
        'image_has_multiple_individuals',
        'resolution_in_ppmm',
        'image_size_bytes',
        'image_file',
        'thumb_small',
        'image_width',
        'image_height',
        'thumb_width',
        'thumb_height',
        'is_validated',
        'last_updated_by',
        'created_at',
        'updated_at',
        'is_deleted',
        'deleted_at',
        'image_preview_large',
    )

    list_filter = ('is_deleted', 'is_validated', 'image_has_multiple_individuals', 'image_institution', 'photographer', 'image_date_taken')

    @admin.display(description='Asset ID', ordering='id')
    def id_short(self, obj):
        return str(obj.id)[:8]

    @admin.display(description="SHA-256")
    def short_sha(self, obj):
        return (obj.image_sha256 or "")[:12]

    @admin.display(description="Size", ordering="image_size_bytes")
    def size_readable(self, obj):
        try:
            from django.contrib.humanize.templatetags.humanize import naturalsize
            return naturalsize(obj.image_size_bytes or 0, binary=True)
        except Exception:
            s = float(obj.image_size_bytes or 0)
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if s < 1024 or unit == "TB":
                    return f"{s:.1f} {unit}"
                s /= 1024.0

    @admin.display(description="Dimensions")
    def dimensions(self, obj):
        if obj.image_width and obj.image_height:
            return f"{obj.image_width}×{obj.image_height}"
        return "—"

    def image_preview(self, obj):
        if obj.image_file:
            return mark_safe(f'<img src="{obj.image_file.url}" style="height: 50px;"/>')
        return "No Image"

    def image_preview_large(self, obj):
        if obj.image_file:
            return mark_safe(f'<img src="{obj.image_file.url}" style="max-height: 400px;"/>')
        return "No Image"


# ---------- ImageLock ----------
@admin.register(ImageLock)
class ImageLockAdmin(admin.ModelAdmin):
    """
    Admin interface for viewing and managing image locks in the data annotator.
    Shows which users are currently viewing/editing which images.
    """
    date_hierarchy = "locked_at"
    list_per_page = 50
    empty_value_display = "—"

    list_display = (
        "id_short",
        "image_asset_id_short",
        "locked_by",
        "locked_at",
        "updated_at",
        "time_held",
        "is_expired_status",
    )

    list_filter = (
        "locked_by",
        "locked_at",
        "updated_at",
    )

    search_fields = (
        "=id",
        "=image_asset__id",
        "locked_by__username",
        "locked_by__email",
    )

    ordering = ("-updated_at",)

    readonly_fields = (
        "id",
        "image_asset",
        "locked_by",
        "locked_at",
        "updated_at",
        "time_held",
        "is_expired_status",
    )

    # Column display methods
    @admin.display(description="Lock ID", ordering="id")
    def id_short(self, obj):
        return str(obj.id)[:8]

    @admin.display(description="Image Asset ID", ordering="image_asset__id")
    def image_asset_id_short(self, obj):
        return str(obj.image_asset.id)[:8]

    @admin.display(description="Time Held")
    def time_held(self, obj):
        """Show how long the lock has been held"""
        from django.utils import timezone
        delta = timezone.now() - obj.locked_at
        minutes = int(delta.total_seconds() / 60)
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}, {minutes % 60} min"

    @admin.display(description="Expired?", boolean=True, ordering="updated_at")
    def is_expired_status(self, obj):
        """Show if the lock has expired (boolean indicator)"""
        return obj.is_expired()

    # Enable actions for cleanup
    actions = ["delete_selected", "cleanup_expired"]

    @admin.action(description="Clean up expired locks")
    def cleanup_expired(self, request, queryset):
        """Custom action to delete expired locks"""
        count = ImageLock.cleanup_expired_locks()
        self.message_user(request, f"Cleaned up {count} expired lock(s).")

    # Permissions
    def has_view_permission(self, request, obj=None):
        return True

    def has_add_permission(self, request):
        return False  # Locks should only be created via API

    def has_change_permission(self, request, obj=None):
        return False  # Locks should not be manually edited

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff  # Staff can manually release locks


# ---------- UpdateBatch ----------
@admin.register(UpdateBatch)
class UpdateBatchAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_per_page = 50
    empty_value_display = "—"

    list_display = (
        "id_short",
        "status_badge",
        "created_at",
        "updated_at",
        "original_filename",
        "size_readable",
        "uploaded_by",
        "short_sha",
        "rows_total",
        "rows_matched",
        "rows_changed",
        "rows_unchanged",
        "rows_failed",
        "validated_at",
        "applied_at",
        "error_excerpt",
    )

    list_filter = ("status", "created_at", "uploaded_by")
    search_fields = ("=id", "original_filename", "sha256")
    ordering = ("-created_at",)

    # ALL FIELDS as readonly
    readonly_fields = (
        "id",
        "uploaded_by",
        "file",
        "original_filename",
        "size_bytes",
        "sha256",
        "status",
        "error_message",
        "species_version_at_validation",
        "species_label_at_validation",
        "rows_total",
        "rows_matched",
        "rows_changed",
        "rows_unchanged",
        "rows_failed",
        "created_at",
        "updated_at",
        "validated_at",
        "applied_at",
        "report_file",
    )

    actions = None

    @admin.display(description='Batch ID', ordering='id')
    def id_short(self, obj):
        return str(obj.id)[:8]

    @admin.display(description="SHA-256")
    def short_sha(self, obj):
        return (obj.sha256 or "")[:12]

    @admin.display(description="Size", ordering="size_bytes")
    def size_readable(self, obj):
        try:
            from django.contrib.humanize.templatetags.humanize import naturalsize
            return naturalsize(obj.size_bytes or 0, binary=True)
        except Exception:
            s = float(obj.size_bytes or 0)
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if s < 1024 or unit == "TB":
                    return f"{s:.1f} {unit}"
                s /= 1024.0

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colors = {
            "staging": "#64748b",
            "validating": "#eab308",
            "validated": "#16a34a",
            "rejected": "#dc2626",
            "applied": "#0ea5e9",
            "apply_failed": "#7c3aed",
        }
        bg = colors.get(obj.status, "#475569")
        return format_html(
            '<span style="padding:2px 8px; border-radius:12px; color:#fff; background:{}; font-size:12px;">{}</span>',
            bg, obj.get_status_display()
        )

    @admin.display(description="Error", ordering="error_message")
    def error_excerpt(self, obj):
        if not obj.error_message:
            return "—"
        msg = obj.error_message.strip()
        return (msg[:90] + "…") if len(msg) > 90 else msg

    def has_view_permission(self, request, obj=None): return True
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


# ---------- Taxon ----------
@admin.register(Taxon)
class TaxonAdmin(admin.ModelAdmin):
    list_per_page = 50
    empty_value_display = "—"

    list_display = (
        "id",
        "valid_species_id",
        "scientific_name",
        "scientific_name_authority",
        "subfamily",
        "tribe",
        "subtribe",
        "genus",
        "species",
        "subspecies",
        "authority",
        "authority_year",
        "original_genus",
        "depth",
        "numchild",
        "created_at",
        "updated_at",
    )

    list_filter = ("subfamily", "tribe", "genus", "depth")
    search_fields = ("valid_species_id", "scientific_name", "genus", "species", "original_genus")
    ordering = ("path",)

    readonly_fields = (
        "id",
        "valid_species_id",
        "scientific_name",
        "scientific_name_authority",
        "subfamily",
        "tribe",
        "subtribe",
        "genus",
        "species",
        "subspecies",
        "authority",
        "authority_year",
        "original_genus",
        "path",
        "depth",
        "numchild",
        "created_at",
        "updated_at",
    )

    def has_view_permission(self, request, obj=None): return True
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


# ---------- Synonym ----------
@admin.register(Synonym)
class SynonymAdmin(admin.ModelAdmin):
    list_per_page = 50
    empty_value_display = "—"

    list_display = (
        "id",
        "name_id",
        "described_scientific_name",
        "described_scientific_name_authority",
        "taxon",
        "genus",
        "species",
        "subspecies",
        "authority",
        "year",
        "created_at",
    )

    list_filter = ("genus", "created_at")
    search_fields = ("name_id", "described_scientific_name", "genus", "species", "taxon__scientific_name")
    ordering = ("described_scientific_name",)

    readonly_fields = (
        "id",
        "taxon",
        "name_id",
        "described_scientific_name",
        "described_scientific_name_authority",
        "genus",
        "species",
        "subspecies",
        "authority",
        "year",
        "created_at",
    )

    def has_view_permission(self, request, obj=None): return True
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


# ---------- CategoryMapping ----------
@admin.register(CategoryMapping)
class CategoryMappingAdmin(admin.ModelAdmin):
    list_per_page = 50
    empty_value_display = "—"

    list_display = (
        "category_id",
        "name",
        "full_name",
        "supercategory",
    )

    list_filter = ("supercategory",)
    search_fields = ("name", "full_name", "category_id")
    ordering = ("category_id",)

    readonly_fields = (
        "category_id",
        "name",
        "full_name",
        "supercategory",
    )

    def has_view_permission(self, request, obj=None): return True
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser




# ---------- Beetle ID game ----------
@admin.register(GameRound)
class GameRoundAdmin(admin.ModelAdmin):
    list_display = ("id", "player", "mode", "started_at", "finished_at")
    list_filter = ("mode", "started_at")
    search_fields = ("player__username",)
    readonly_fields = ("id", "player", "mode", "items", "started_at", "finished_at")


@admin.register(GameAnswer)
class GameAnswerAdmin(admin.ModelAdmin):
    list_display = (
        "player", "mode", "is_check", "skipped", "subfamily", "tribe", "genus", "species",
        "pair_answer", "correct_subfamily", "correct_tribe", "correct_genus", "correct_species",
        "answered_at",
    )
    list_filter = ("mode", "is_check", "skipped", "answered_at")
    search_fields = ("player__username", "genus", "species")
    raw_id_fields = ("round", "roi", "roi_b")
    list_select_related = ("player",)


@admin.register(PlayerSkill)
class PlayerSkillAdmin(admin.ModelAdmin):
    list_display = ("player", "rank", "branch", "correct", "judged", "covered", "required", "children_done",
                    "children_needed", "children_total", "proven", "proven_at")
    list_filter = ("rank", "proven")
    search_fields = ("player__username", "branch")
    readonly_fields = ("player", "rank", "branch", "correct", "judged", "lower_bound", "required", "covered",
                       "children_total", "children_needed", "children_done", "proven", "proven_at", "updated_at")


@admin.register(RetroCredit)
class RetroCreditAdmin(admin.ModelAdmin):
    """Read-only: made when a beetle is validated after a player answered it (game_scoring.recompute)."""
    list_display = ("player", "validated_name", "points_before", "points_after", "validated_at", "created_at", "seen_at")
    search_fields = ("player__username", "validated_name")
    readonly_fields = ("answer", "player", "points_before", "points_after", "validated_name", "validated_at",
                       "created_at", "seen_at")


@admin.register(SpeciesDiscovery)
class SpeciesDiscoveryAdmin(admin.ModelAdmin):
    """Read-only: found by the game (game_discoveries.find)."""
    list_display = ("player", "genus", "species", "roi", "created_at", "seen_at")
    search_fields = ("player__username", "genus", "species")
    readonly_fields = ("player", "roi", "answer", "genus", "species", "created_at", "seen_at")


@admin.register(PlayerScore)
class PlayerScoreAdmin(admin.ModelAdmin):
    """Read-only: recomputed from the answers (manage.py recompute_game_scores)."""
    list_display = ("player", "score", "rating", "accuracy", "judged", "viewed", "labelled", "updated_at")
    search_fields = ("player__username",)
    readonly_fields = list_display

    def has_add_permission(self, request):
        return False


@admin.register(GamePreference)
class GamePreferenceAdmin(admin.ModelAdmin):
    list_display = ("player", "focus_rank", "focus_value", "updated_at")
    search_fields = ("player__username", "focus_value")


@admin.register(AnswerPoints)
class AnswerPointsAdmin(admin.ModelAdmin):
    """Read-only: recomputed from the answers."""
    list_display = ("answer", "points", "basis", "computed_at")
    list_filter = ("basis",)
    readonly_fields = ("answer", "points", "basis", "detail", "computed_at")

    def has_add_permission(self, request):
        return False


@admin.register(LabelReview)
class LabelReviewAdmin(admin.ModelAdmin):
    list_display = ("roi", "decision", "genus", "species", "trusted_rank", "answers", "reviewed_by", "reviewed_at")
    list_filter = ("decision", "trusted_rank", "reviewed_at")
    raw_id_fields = ("roi", "taxon")


@admin.register(RoiDifficulty)
class RoiDifficultyAdmin(admin.ModelAdmin):
    list_display = ("roi", "model_difficulty", "model_name", "game_difficulty", "game_answers", "updated_at")
    raw_id_fields = ("roi",)


@admin.register(GameReport)
class GameReportAdmin(admin.ModelAdmin):
    list_display = ("roi", "reporter", "reason", "status", "was_validated", "created_at", "resolved_by", "resolved_at")
    list_filter = ("status", "reason", "created_at")
    search_fields = ("reporter__username", "note")
    raw_id_fields = ("roi", "answer")


# ---------- PathogenInteraction ----------
@admin.register(PathogenInteraction)
class PathogenInteractionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "beetle_host", "pathogen", "category", "ecological_relationship",
        "country_or_region", "year", "record_block_id", "record_number",
    )
    list_filter = ("origin", "category", "ecological_relationship", "validation_type")
    search_fields = ("=id", "beetle_host", "beetle_host_id", "pathogen", "record_block_id", "source", "title")
    readonly_fields = ("id", "created_at", "updated_at")
    list_per_page = 50
    empty_value_display = "—"


@admin.register(ModelPrediction)
class ModelPredictionAdmin(admin.ModelAdmin):
    list_display = ("roi", "valid_species_id", "confidence", "model_name", "model_version", "uploaded_by", "created_at")
    list_filter = ("model_name", "model_version", "created_at")
    search_fields = ("=roi__id", "valid_species_id", "model_name")
    raw_id_fields = ("roi", "taxon")
    readonly_fields = ("id", "created_at")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "status", "granted_role", "created_at", "decided_by", "decided_at")
    list_filter = ("status", "granted_role")
    search_fields = ("name", "email", "affiliation")
    raw_id_fields = ("user", "decided_by")
    readonly_fields = ("id", "created_at", "notified_at", "notify_error")


@admin.register(InteractionProposal)
class InteractionProposalAdmin(admin.ModelAdmin):
    list_display = ("beetle_name", "partner_name", "category", "status", "score", "source_doi", "source_year", "reviewed_by")
    list_filter = ("status", "category", "source_db", "collector")
    search_fields = ("beetle_name", "partner_name", "source_doi", "source_title")
    raw_id_fields = ("taxon", "published_as", "reviewed_by", "created_by")
    readonly_fields = ("id", "created_at")
    list_per_page = 50


@admin.register(AreaGrant)
class AreaGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "area", "granted_by", "created_at")
    list_filter = ("area",)
    raw_id_fields = ("user", "granted_by")


@admin.register(SiteNotice)
class SiteNoticeAdmin(admin.ModelAdmin):
    """Usually edited on My account -> Site notice (it clears the cache there; here it shows within a minute)."""
    list_display = ("text", "active", "updated_by", "updated_at")
    raw_id_fields = ("updated_by",)


@admin.register(PredictionUpload)
class PredictionUploadAdmin(admin.ModelAdmin):
    """Model predictions files uploaded on Data Management (checked and saved in the background)."""
    list_display = ("original_filename", "status", "percent", "dry_run", "uploaded_by", "created_at")
    list_filter = ("status", "dry_run")
    raw_id_fields = ("uploaded_by",)
    readonly_fields = ("result", "created_at", "finished_at")


@admin.register(RoiName)
class RoiNameAdmin(admin.ModelAdmin):
    """Every name an ROI was given, with its identification tier (the ROI shows the most reliable)."""
    list_display = ("roi", "valid_species_id", "tier", "detail", "added_by", "created_at")
    list_filter = ("tier",)
    search_fields = ("valid_species_id", "detail")
    raw_id_fields = ("roi", "taxon", "added_by")


@admin.register(GameTuning)
class GameTuningAdmin(admin.ModelAdmin):
    """Scoring overrides; usually changed on the game's Scoring page, which explains each one and checks its limits."""
    list_display = ("key", "value", "updated_by", "updated_at")
    raw_id_fields = ("updated_by",)
