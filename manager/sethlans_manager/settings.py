# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path
import configparser
import logging
import os

from shared.frozen_paths import (
    get_data_dir,
    get_frontend_dist_dir,
    get_manager_dir,
    is_frozen,
)

# Build paths inside the project like this: BASE_DIR / 'subdir'.
if is_frozen():
    BASE_DIR = get_manager_dir()
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# --- Manager Configuration (manager.ini) ---
# Hierarchy: env vars > manager.ini > defaults
_INSECURE_DEFAULT_KEY = (
    'django-insecure-^&r@p#+r6h*!@!1u=l!0j_z%z!%^n#b=2#h&l16b%c!0609t'
)

_config = configparser.ConfigParser()
if is_frozen():
    _config_file_path = get_data_dir('manager') / 'manager.ini'
else:
    _config_file_path = BASE_DIR / 'manager.ini'
if _config_file_path.exists():
    _config.read(_config_file_path)


def _get_config(section, key, env_var, default):
    """Read a setting: env var > manager.ini > default."""
    value = os.getenv(env_var)
    if value is not None:
        return value
    if _config.has_option(section, key):
        return _config.get(section, key)
    return default


# --- Security Settings ---
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

SECRET_KEY = _get_config(
    'security', 'secret_key', 'SETHLANS_SECURITY_SECRET_KEY',
    _INSECURE_DEFAULT_KEY
)

_debug_raw = _get_config(
    'security', 'debug', 'SETHLANS_SECURITY_DEBUG', 'False'
)
DEBUG = _debug_raw.lower() in ('true', '1', 'yes')

_hosts_raw = _get_config(
    'security', 'allowed_hosts', 'SETHLANS_SECURITY_ALLOWED_HOSTS', '*'
)
ALLOWED_HOSTS = [
    h.strip() for h in _hosts_raw.split(',') if h.strip()
]

# Warn if using the insecure default secret key
_logger = logging.getLogger('django')
if SECRET_KEY == _INSECURE_DEFAULT_KEY:
    if not DEBUG:
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured(
            "Cannot run with the default SECRET_KEY when DEBUG is False. "
            "Set a unique key in manager.ini or "
            "SETHLANS_SECURITY_SECRET_KEY."
        )
    _logger.warning(
        "Using insecure default SECRET_KEY. Set a unique key in "
        "manager.ini [security] or SETHLANS_SECURITY_SECRET_KEY "
        "environment variable before deploying to production."
    )

# Enrollment key for worker registration
ENROLLMENT_KEY = _get_config(
    'security', 'enrollment_key', 'SETHLANS_SECURITY_ENROLLMENT_KEY', ''
)


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',   # Must be before staticfiles
    'django.contrib.staticfiles',
    # Third-party apps
    'rest_framework',
    'rest_framework.authtoken',
    'django_filters',
    'drf_spectacular',
    # Your custom apps here
    'workers',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'sethlans_manager.middleware.setup_gate.SetupGateMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'sethlans_manager.urls'

if is_frozen():
    _template_dir = get_frontend_dist_dir()
else:
    _template_dir = BASE_DIR / 'frontend' / 'dist' / 'browser' / 'browser'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [_template_dir],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'sethlans_manager.wsgi.application'
ASGI_APPLICATION = 'sethlans_manager.asgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# Hierarchy: env vars > manager.ini [database] > SQLite default
from .db_config import build_database_config  # noqa: E402
DATABASES = build_database_config(_config, _config_file_path)


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
if is_frozen():
    STATIC_ROOT = get_data_dir('manager') / 'staticfiles'
    _FRONTEND_DIST = get_frontend_dist_dir()
else:
    STATIC_ROOT = BASE_DIR / 'staticfiles'
    _FRONTEND_DIST = (
        BASE_DIR / 'frontend' / 'dist' / 'browser' / 'browser'
    )

STATICFILES_DIRS = [_FRONTEND_DIST] if _FRONTEND_DIST.is_dir() else []
if not STATICFILES_DIRS:
    _logger.warning(
        "STATICFILES_DIRS is empty — frontend dist directory "
        "not found at %s", _FRONTEND_DIST,
    )

# Serve Angular dist files (JS chunks, CSS) at root URL paths.
# Angular outputs files like /main-xxx.js and /chunk-xxx.js at root level,
# not under /static/. WHITENOISE_ROOT makes these accessible without a prefix.
WHITENOISE_ROOT = _FRONTEND_DIST if _FRONTEND_DIST.is_dir() else None
if WHITENOISE_ROOT is None:
    _logger.warning(
        "WHITENOISE_ROOT is None — frontend dist directory "
        "not found at %s", _FRONTEND_DIST,
    )

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Logging Configuration ---
from .logging_config import LOGGING  # noqa: E402, F401

# --- Media Files (User Uploads) Configuration ---
MEDIA_URL = '/media/'
if is_frozen():
    _default_media_root = str(get_data_dir('manager') / 'media')
else:
    _default_media_root = os.path.join(BASE_DIR, 'media')
MEDIA_ROOT = os.getenv('SETHLANS_MEDIA_ROOT', _default_media_root)

# --- Session Cookie Configuration ---
SESSION_COOKIE_HTTPONLY = True  # Default, but explicit for clarity
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = True  # Safe now that HTTPS is enforced
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 86400  # 24 hours

# --- CSRF Cookie Configuration ---
CSRF_COOKIE_SECURE = True

# --- HSTS ---
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# --- DRF Configuration ---
from .drf_config import REST_FRAMEWORK, SPECTACULAR_SETTINGS  # noqa: E402, F401

# Delete old thumbnail files before saving new ones
WORKERS_DELETE_OLD_THUMBNAILS = True

# --- Upload Size Limits ---
# Maximum size for request body (100MB) — covers multipart file uploads
DATA_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100 MB
# Maximum size for a single uploaded file (100MB)
FILE_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100 MB
