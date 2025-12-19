from django.contrib import admin
from django.utils.html import format_html
from .models import UploadBatch, Beetles, DownloadJob
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
        "total_requested", "status", "error_message", "tsv_file", "zip_file",
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
    date_hierarchy = "image_date_taken"
    list_per_page = 50
    empty_value_display = "—"

    # columns in admin table view
    list_display = (
        "id_short",
        "depicts_valid_name_id",
        "alternative_id",
        "photographer",
        "collection_country",
        "collection_stateProvince",
        "image_date_taken",
    )
    # right-hand sidebar filters
    list_filter = (
        "collection_country",
        "collection_stateProvince",
        "photographer",
        "image_institution",
        "image_has_multiple_individuals",
        "specimen_sex",
        "specimen_type_status",
        )
    # can use LIKE queries against these fields in search box
    search_fields = (
        "=id", 
        "depicts_valid_name_id",
        "alternative_id",
        "depicts_described_name_id",
        "depicts_name_verbatim",
        "photographer",
        "image_institution",
        "collection_country",
        "collection_stateProvince",
        "specimen_notes",
        "image_notes",
        )
    # default sort order for the table view
    ordering = ("depicts_valid_name_id", "-image_date_taken")

    # read only in detail view
    readonly_fields = (
        "id",
        "full_path_at_import",
        "alternative_id",
        "image_institution",
        "photographer",
        "image_email",
        "photo_usage_statement",
        "aspect",
        "resolution_in_ppmm",
        "image_notes",
        "image_date_taken",
        "image_has_multiple_individuals",
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

    def has_view_permission(self, request, obj=None): return True
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False  # remove this to allow reverting changes in admin
    def has_delete_permission(self, request, obj=None): return False
