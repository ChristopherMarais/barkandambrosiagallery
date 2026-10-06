import re

from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import Http404
from django.views.static import serve

from beetlesgallery.beetles_app import views as beetles_views
from beetlesgallery.beetles_app import chunked_upload
from beetlesgallery.beetles_app import game_views
from beetlesgallery.beetles_app import game_tuning_views
from beetlesgallery.beetles_app import site_notice
from beetlesgallery.beetles_app import bulk_validate
from beetlesgallery.beetles_app import roi_reports
from beetlesgallery.beetles_app import interaction_views
from beetlesgallery.beetles_app import interaction_proposals_views
from beetlesgallery.beetles_app import interaction_upload_views
from beetlesgallery.beetles_app import interaction_downloads
from beetlesgallery.beetles_app import access_views
from beetlesgallery.beetles_app.views import LoginViewWithRedirectMessage, PostOnlyLogoutView

urlpatterns = [
    path("admin/tools/valid-species/", beetles_views.admin_valid_species, name="admin_valid_species"),
    path("admin/tools/described-names/", beetles_views.admin_described_names, name="admin_described_names"),
    path('admin/', admin.site.urls),
    
    # --- Auth ---
    path("accounts/login/", LoginViewWithRedirectMessage.as_view(template_name="accounts/signin.html"), name="login"),
    path("accounts/logout/", PostOnlyLogoutView.as_view(), name="logout"),
    path("accounts/signup/", beetles_views.signup, name="signup"),
    path("accounts/request-access/", access_views.request_access, name="request_access"),
    path("accounts/request-access/sent/", access_views.request_access_sent, name="request_access_sent"),
    path("accounts/set-password/<uidb64>/<token>/", access_views.SetPasswordView.as_view(), name="password_set"),
    path("accounts/verify-email/<uidb64>/<token>/", access_views.verify_email, name="verify_email"),
    path("accounts/password-reset/", access_views.ResetRequestView.as_view(), name="password_reset"),
    path("accounts/password-reset/sent/", access_views.password_reset_done, name="password_reset_done"),
    path("accounts/me/", beetles_views.my_account, name="my_account"),

    # --- Pages ---
    # 1. Root URL -> Landing View (Sidebar "image_browser" links here)
    path('', beetles_views.landing, name='image_browser'),
    
    # 2. /beetles/ -> Gallery View (Sidebar "Beetles" links here)
    path('beetles/', beetles_views.gallery, name='beetles_image_browser'),

    # 3. /taxonomy/ -> Taxonomy Browser (Sidebar "Taxonomy Browser" links here)
    path('taxonomy/', beetles_views.taxonomy_browser, name='taxonomy_browser'),
    path('taxonomy/described-names/', beetles_views.described_names_for_species, name='described_names_for_species'),
    path('taxonomy/species-images/', beetles_views.species_images, name='species_images'),
    path('taxonomy/search/', beetles_views.taxonomy_search, name='taxonomy_search'),

    # 4. /interactions/ -> Ecological Interactions (Pathogen & Parasite Database Preview)
    path('interactions/', beetles_views.interactions_preview, name='interactions_preview'),
    path('interactions/data/records.json', interaction_views.interactions_records, name='interactions_records'),
    path('interactions/data/hosts.json', interaction_views.interactions_hosts, name='interactions_hosts'),
    path('interactions/data/references.json', interaction_views.interactions_references, name='interactions_references'),
    path('interactions/review/', interaction_views.interaction_review, name='interaction_review'),
    path('interactions/proposals/', interaction_proposals_views.upload_interaction_proposals, name='upload_interaction_proposals'),
    path('interactions/upload/initial-file.csv', interaction_upload_views.interactions_initial_file, name='interactions_initial_file'),
    path('interactions/upload/', interaction_upload_views.upload_interactions, name='upload_interactions'),
    path('interactions/download/<slug:name>.<str:ext>', interaction_downloads.interactions_download, name='interactions_download'),
    path('interactions/export.csv', interaction_upload_views.interactions_export, name='interactions_export'),

    path('beetles/<uuid:beetle_id>/', beetles_views.beetle_detail, name='beetle_detail'),
    path('beetles/<uuid:beetle_id>/report/', roi_reports.report_roi, name='report_roi'),

    # --- Tools ---
    path('upload/', beetles_views.upload_file, name='upload'),
    path('upload/chunk/', chunked_upload.upload_chunk, name='upload_chunk'),
    path('upload/chunk/status/', chunked_upload.upload_chunk_status, name='upload_chunk_status'),
    path("my-uploads/", beetles_views.data_management, name="data_management"),
    path("events/", beetles_views.stream_updates, name="stream_updates"),
    path("downloads/start/", beetles_views.start_batch_download, name="start_batch_download"),
    path("updates/", beetles_views.update_upload, name="update_upload"),
    path("reference/download/", beetles_views.download_taxonomy_ref, name="download_taxonomy_ref"),
    path("reference/download-described-names/", beetles_views.download_described_names_ref, name="download_described_names_ref"),
    path("reference/archive/<str:ref_type>/<str:filename>/", beetles_views.download_taxonomy_archive, name="download_taxonomy_archive"),
    path('tools/classify/', beetles_views.tool_classify, name='tool_classify'),
    path('tools/annotate/', beetles_views.tool_annotate, name='tool_annotate'),
    path('tools/predictions/', beetles_views.upload_predictions, name='upload_predictions'),
    path('tools/predictions/<uuid:job_id>/', beetles_views.upload_predictions_status, name='upload_predictions_status'),
    path('tools/bulk-validate/', bulk_validate.bulk_validate, name='bulk_validate'),
    path('tools/access-requests/', access_views.access_requests, name='access_requests'),
    path('tools/site-notice/', site_notice.edit, name='site_notice'),

    # --- Beetle ID game ---
    path('game/', game_views.game_home, name='game_home'),
    path('game/play/<str:mode>/', game_views.game_play, name='game_play'),
    path('game/api/start/', game_views.game_start, name='game_start'),
    path('game/api/exit/', game_views.game_exit, name='game_exit'),
    path('game/me/', game_views.game_report, name='game_report'),
    path('game/how-it-works/', game_views.game_how, name='game_how'),
    path('game/unlocks/', game_views.game_unlocks, name='game_unlocks'),
    path('game/api/prefs/', game_views.game_prefs, name='game_prefs'),
    path('game/api/warm/', game_views.game_warm_others, name='game_warm'),
    path('game/checked/', game_views.game_checked_page, name='game_checked'),
    path('game/history/', game_views.game_history, name='game_history'),
    path('game/api/report-item/', game_views.game_report_item, name='game_report_item'),
    path('game/staff/unlocks/', game_views.game_staff_unlocks, name='game_staff_unlocks'),
    path('game/leaderboard/', game_views.game_leaderboard, name='game_leaderboard'),
    path('game/players/<int:user_id>/profile/', game_views.game_profile, name='game_profile'),
    path('game/expertise/', game_views.game_expertise, name='game_expertise'),
    path('game/players/<int:user_id>/expertise/', game_views.game_expertise, name='game_player_expertise'),
    path('game/rounds/<uuid:round_id>/', game_views.game_round_review, name='game_round_review'),
    path('game/api/report/', game_views.game_report_roi, name='game_report_roi'),
    path('game/api/reports/<uuid:roi_id>/resolve/', game_views.game_resolve_reports, name='game_resolve_reports'),
    path('game/players/<int:user_id>/', game_views.game_player_report, name='game_player_report'),
    path('game/api/proposals/', game_views.game_proposals, name='game_proposals'),
    path('game/api/proposals/<uuid:roi_id>/review/', game_views.game_proposal_review, name='game_proposal_review'),
    path('game/api/applied/<uuid:roi_id>/revert/', game_views.game_applied_revert, name='game_applied_revert'),
    path('game/api/round/<uuid:round_id>/answer/', game_views.game_answer, name='game_answer'),
    path('game/api/round/<uuid:round_id>/review/<int:index>/', game_views.game_past_review, name='game_past_review'),
    path('game/api/round/<uuid:round_id>/crop/<int:index>/<int:image>/<str:size>/', game_views.game_crop, name='game_crop'),
    path('game/api/taxa/', game_views.game_taxa, name='game_taxa'),
    path('game/review/', game_views.game_review, name='game_review'),
    path('game/scoring/', game_tuning_views.scoring, name='game_scoring'),
    path('game/scoring/rescore/', game_tuning_views.rescore, name='game_scoring_rescore'),
    path('game/review/<str:kind>.csv', game_views.game_export, name='game_export'),

    # --- API ---
    path('api/v1/', include('beetlesgallery.beetles_app.api.urls')),
]

# Files in the media folder that are never served: database dumps, logs and hidden files.
# (The backup used to write its database dump into the media folder, where this view would have served it.)
PRIVATE_MEDIA = re.compile(r"(^|/)\.|\.(sql|sql\.gz|dump|log)$", re.I)


def media_serve_with_cache(request, path, document_root=None, show_indexes=False):
    if PRIVATE_MEDIA.search(path):
        raise Http404
    response = serve(request, path, document_root, show_indexes)
    # Cache thumbnails and images for 30 days in browser & Cloudflare CDN
    response["Cache-Control"] = "public, max-age=2592000, immutable"
    return response

# Serve Media Files (User Uploads) manually since we don't have Nginx
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', media_serve_with_cache, {
        'document_root': settings.MEDIA_ROOT,
    }),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)