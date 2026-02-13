from django.contrib import admin
from django.utils.html import format_html, mark_safe
from .models import UploadBatch, Beetles, ImageAsset, DownloadJob, BoundingBox
from simple_history.admin import SimpleHistoryAdmin


# ---------- DownloadJob ----------
@admin.register(DownloadJob)
class DownloadJobAdmin(admin.ModelAdmin):
    list_display = ("created_at", "requested_by", "selection_mode", "status", "total_requested")
    list_filter = ("status", "selection_mode", "created_at")
    search_fields = ("id", "query_string", "selected_ids_json", "requested_by__username", "requested_by__email")
    readonly_fields = (
        "id", "created_at", "started_at", "finished_at", "expires_at",
        "selection_mode", "query_string", "selected_ids_json",
        "total_requested", "status", "error_message", "csv_file", "zip_file",
        "requested_by",
    )


# ---------- UploadBatch ----------
@admin.register(UploadBatch)
class UploadBatchAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_select_related = ("uploaded_by",)
    list_per_page = 50
    empty_value_display = "—"

    # columns in admin table view
    list_display = (
        "id",
        "status",
        "created_at",
        "status_badge",
        "original_filename",
        "size_readable",
        "uploaded_by",
        "short_sha",
        "file_link",
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

    # read only in detail view
    readonly_fields = (
        "id", 
        "file", 
        "size_bytes", 
        "sha256", 
        "status",
        "error_message", 
        "created_at", 
        "updated_at",
        "validated_at", 
        "imported_at", 
        "uploaded_by",
    )

    # hides the admin’s bulk actions dropdown
    actions = None


    # ---- Column renderers ----
    # helper methods that return a rendered value for use in list_display

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

    @admin.display(description="File")
    def file_link(self, obj):
        if not obj.file:
            return "—"
        # In DEBUG this will be a clickable link; in prod ensure MEDIA is served.
        try:
            return format_html('<a href="{}" target="_blank">open</a>', obj.file.url)
        except Exception:
            # Storage can’t build a URL (e.g., not web-served) — show logical path instead.
            return format_html('<code>{}</code>', obj.file.name)

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

    # LIST DISPLAY: We must use custom methods for fields that moved to ImageAsset
    list_display = (
        "id_short",
        "depicts_valid_name_id",
        "alternative_id",
        "get_photographer",      # Changed
        "collection_country",
        "collection_stateProvince",
        "get_date_taken",        # Changed
    )

    # LIST FILTERS: Use double-underscore to filter across the relationship
    list_filter = (
        "collection_country",
        "collection_stateProvince",
        "image_asset__photographer",                 # Changed
        "image_asset__image_institution",            # Changed
        "image_asset__image_has_multiple_individuals", # Changed
        "specimen_sex",
        "specimen_type_status",
    )

    # SEARCH FIELDS: Use double-underscore for related search
    search_fields = (
        "=id", 
        "depicts_valid_name_id",
        "alternative_id",
        "depicts_described_name_id",
        "depicts_name_verbatim",
        "image_asset__photographer",        # Changed
        "image_asset__image_institution",   # Changed
        "collection_country",
        "collection_stateProvince",
        "specimen_notes",
        "image_asset__image_notes",         # Changed
    )

    ordering = ("depicts_valid_name_id", "-image_asset__image_date_taken")

    # READONLY FIELDS: Point to custom methods below so we can see the data
    readonly_fields = (
        "id",
        "image_preview_in_beetle", # New: Show image directly here!
        "get_full_path",
        "alternative_id",
        "get_institution",
        "get_photographer",
        "get_email",
        "get_usage_statement",
        "aspect",
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
    )

    # hides the admin’s bulk actions dropdown
    actions = None

    def id_short(self, obj):
        return str(obj.id)[:8]
    id_short.short_description = "ID"

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
    def has_delete_permission(self, request, obj=None): return False

@admin.register(ImageAsset)
class ImageAssetAdmin(admin.ModelAdmin):
    list_display = ('id', 'image_preview', 'image_institution', 'photographer', 'created_at')
    search_fields = ('image_sha256', 'full_path_at_import', 'image_institution', 'photographer')
    readonly_fields = ('image_preview_large', 'created_at', 'updated_at')
    list_filter = ('image_institution', 'photographer')

    def image_preview(self, obj):
        if obj.image_file:
            return mark_safe(f'<img src="{obj.image_file.url}" style="height: 50px;"/>')
        return "No Image"
    
    def image_preview_large(self, obj):
        if obj.image_file:
            return mark_safe(f'<img src="{obj.image_file.url}" style="max-height: 400px;"/>')
        return "No Image"


# ---------- BoundingBox ----------
@admin.register(BoundingBox)
class BoundingBoxAdmin(admin.ModelAdmin):
    list_display = (
        'id_short',
        'image_asset',
        'label',
        'source',
        'confidence',
        'is_validated',
        'created_by',
        'created_at',
    )
    list_filter = ('source', 'is_validated', 'created_by', 'validated_by', 'created_at')
    search_fields = (
        '=id',
        'label',
        'image_asset__image_sha256',
        'beetle__id',
        'notes',
    )
    readonly_fields = (
        'id',
        'image_asset',
        'beetle',
        'x',
        'y',
        'width',
        'height',
        'label',
        'confidence',
        'source',
        'created_by',
        'created_at',
        'updated_at',
        'is_validated',
        'validated_by',
        'validated_at',
        'notes',
        'box_area',
        'coordinates_display',
    )
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    @admin.display(description='ID')
    def id_short(self, obj):
        return str(obj.id)[:8]

    @admin.display(description='Area')
    def box_area(self, obj):
        return f"{obj.area:.4f}"

    @admin.display(description='Coordinates')
    def coordinates_display(self, obj):
        return f"x:{obj.x:.3f}, y:{obj.y:.3f}, w:{obj.width:.3f}, h:{obj.height:.3f}"

    def has_view_permission(self, request, obj=None):
        return True

    def has_add_permission(self, request):
        return False  # Annotations should be created via the annotation tool

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff