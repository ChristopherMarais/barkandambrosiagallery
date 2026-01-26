from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

from beetlesgallery.beetles_app import views as beetles_views
from beetlesgallery.beetles_app.views import LoginViewWithRedirectMessage, PostOnlyLogoutView

urlpatterns = [
    path("admin/tools/valid-species/", beetles_views.admin_valid_species, name="admin_valid_species"),
    path('admin/', admin.site.urls),
    
    # --- Auth ---
    path("accounts/login/", LoginViewWithRedirectMessage.as_view(template_name="accounts/signin.html"), name="login"),
    path("accounts/logout/", PostOnlyLogoutView.as_view(), name="logout"),
    path("accounts/signup/", beetles_views.signup, name="signup"),
    path("accounts/me/", beetles_views.my_account, name="my_account"),

    # --- Pages ---
    # 1. Root URL -> Landing View (Sidebar "Home" links here)
    path('', beetles_views.landing, name='home'),
    
    # 2. /beetles/ -> Gallery View (Sidebar "Beetles" links here)
    path('beetles/', beetles_views.gallery, name='beetles_home'),

    path('beetles/<uuid:beetle_id>/', beetles_views.beetle_detail, name='beetle_detail'),
    path("beetles/add_specimen/<uuid:image_id>/", beetles_views.create_specimen_for_image, name="create_specimen_for_image"),

    # --- Tools ---
    path('upload/', beetles_views.upload_file, name='upload'),
    path("my-uploads/", beetles_views.my_uploads, name="my_uploads"),
    path("events/", beetles_views.stream_updates, name="stream_updates"),
    path("downloads/start/", beetles_views.start_batch_download, name="start_batch_download"),
    path("updates/", beetles_views.update_upload, name="update_upload"),
    path("reference/download/", beetles_views.download_taxonomy_ref, name="download_taxonomy_ref"),
    path('update_single/<uuid:beetle_id>/', beetles_views.update_single_beetle, name='update_single_beetle'),
    path('tools/classify/', beetles_views.tool_classify, name='tool_classify'),

    # --- API ---
    path('api/v1/', include('beetlesgallery.beetles_app.api.urls')),
]

# Serve Media Files (User Uploads) manually since we don't have Nginx
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {
        'document_root': settings.MEDIA_ROOT,
    }),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)