# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path
import configparser
import os
import logging

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Manager Configuration (manager.ini) ---
# Hierarchy: env vars > manager.ini > defaults
_INSECURE_DEFAULT_KEY = (
    'django-insecure-^&r@p#+r6h*!@!1u=l!0j_z%z!%^n#b=2#h&l16b%c!0609t'
)

_config = configparser.ConfigParser()
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
    'security', 'debug', 'SETHLANS_SECURITY_DEBUG', 'True'
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
    _logger.warning(
        "Using insecure default SECRET_KEY. Set a unique key in "
        "manager.ini [security] or SETHLANS_SECURITY_SECRET_KEY "
        "environment variable before deploying to production."
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
    'django_filters',
    'drf_spectacular',
    # Your custom apps here
    'workers',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'sethlans_manager.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'frontend' / 'dist' / 'browser' / 'browser'],
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


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# Use environment variable for DB name, otherwise default to db.sqlite3
DB_NAME = os.getenv('SETHLANS_DB_NAME', BASE_DIR / 'db.sqlite3')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': DB_NAME, # Use the new variable here
        'OPTIONS': {
            'timeout': 30,
        },
    }
}


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
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'frontend' / 'dist' / 'browser' / 'browser']

# Serve Angular dist files (JS chunks, CSS) at root URL paths.
# Angular outputs files like /main-xxx.js and /chunk-xxx.js at root level,
# not under /static/. WHITENOISE_ROOT makes these accessible without a prefix.
WHITENOISE_ROOT = BASE_DIR / 'frontend' / 'dist' / 'browser' / 'browser'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- NEW: Logging Configuration ---
# Get default log level from environment variable, fallback to INFO for production-like verbosity
LOG_LEVEL = os.getenv('DJANGO_LOG_LEVEL', 'INFO').upper()

# Create logs directory if it doesn't exist
LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False, # Keep Django's default loggers

    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
        'standard': { # Custom format for standard application logs
            'format': '[{asctime}] [{levelname}] [{name}] {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
            'level': 'DEBUG',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'manager.log',
            'level': 'INFO',
            'formatter': 'standard',
            'maxBytes': 1024 * 1024 * 5, # 5 MB
            'backupCount': 5,
        },
    },
    'loggers': {
        '': {
            'handlers': ['console', 'file'],
            'level': LOG_LEVEL,
            'propagate': True,
        },
        'workers': {
            'handlers': ['console', 'file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# --- NEW: Media Files (User Uploads) Configuration ---
MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('SETHLANS_MEDIA_ROOT', os.path.join(BASE_DIR, 'media'))

# --- DRF Configuration ---
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# --- DRF Spectacular Configuration ---
SPECTACULAR_SETTINGS = {
    'TITLE': 'Sethlans Reborn API',
    'DESCRIPTION': 'RESTful API for the distributed Blender rendering system.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# Delete old thumbnail files before saving new ones
WORKERS_DELETE_OLD_THUMBNAILS = True

# --- Upload Size Limits ---
# Maximum size for request body (100MB) — covers multipart file uploads
DATA_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100 MB
# Maximum size for a single uploaded file (100MB)
FILE_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100 MB
