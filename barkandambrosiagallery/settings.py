import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-dev-key")

# DEBUG is True unless explicitly set to False in env
DEBUG = os.environ.get("DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost 127.0.0.1 0.0.0.0").split(" ")

CSRF_TRUSTED_ORIGINS = [
    "https://barkandambrosiagallery.org",
    "https://www.barkandambrosiagallery.org",
]

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'django.contrib.sites',
    
    # Third party (from your pixi.toml)
    'simple_history',
    'crispy_forms',

    # My Apps (Copied folders)
    'beetles_app.apps.BeetlesAppConfig',
    'articles.apps.ArticlesConfig',
]

SITE_ID = 1

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    "whitenoise.middleware.WhiteNoiseMiddleware",  # <--- Added for Docker static files
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware', # <--- Added for history
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'barkandambrosiagallery.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # <--- Points to the folder you copied
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'articles.context_processors.recent_articles',
                "beetles_app.context_processors.species_ref_status",
            ],
        },
    },
]

WSGI_APPLICATION = 'barkandambrosiagallery.wsgi.application'

# Database
# Uses Postgres in Docker (env vars), falls back to Sqlite locally if variables missing
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'beetles_db'),
        'USER': os.environ.get('POSTGRES_USER', 'beetles_user'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'devpass'),
        'HOST': os.environ.get('POSTGRES_HOST', 'db'), # 'db' matches docker-compose service
        'PORT': '5432',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] # Points to the folder you copied

# Media Files (User uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Auth redirects
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "upload"
LOGOUT_REDIRECT_URL = "home"

# Email (Console for dev)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Celery Configuration (Redis)
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

# Upload Safety
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = [".xlsx"]
FILE_UPLOAD_TEMP_DIR = os.path.join(BASE_DIR, "tmp_uploads")
os.makedirs(FILE_UPLOAD_TEMP_DIR, exist_ok=True)