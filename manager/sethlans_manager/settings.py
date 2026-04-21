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
    'security', 'secret_key', 'SETHLANS_SECURITY_SECRET_KEY', None
)

_logger = logging.getLogger('django')

if SECRET_KEY is None:
    # No explicit override — fall back to a persisted, auto-generated
    # key in the manager data dir. First boot generates it; subsequent
    # boots read it. See workers.services.secret_key.
    from workers.services.secret_key import load_or_create_secret_key
    SECRET_KEY = load_or_create_secret_key(get_data_dir('manager'))
elif SECRET_KEY == _INSECURE_DEFAULT_KEY:
    # Someone deliberately set the insecure default via env/ini.
    # Warn loudly but don't crash — dev convenience only.
    _logger.warning(
        "SECRET_KEY is set to the known insecure default. Remove the "
        "override from manager.ini / SETHLANS_SECURITY_SECRET_KEY to "
        "let the manager auto-generate a unique persisted key."
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
    # UrlconfOriginMiddleware must run FIRST so that every subsequent
    # middleware (and ultimately the URL resolver) sees the correct
    # per-listener URLconf pinned on the request.  See
    # ``sethlans_manager.middleware.urlconf_origin`` and the
    # waitress-migration-manager spec (Phase 2).
    'sethlans_manager.middleware.urlconf_origin.UrlconfOriginMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise must come BEFORE SetupGateMiddleware so that static
    # assets (Angular's root-served /main-*.js, /polyfills-*.js,
    # /styles-*.css, etc.) are served directly instead of being
    # redirected to /setup/.  Unknown paths fall through to the gate,
    # preserving redirect-to-/setup/ for browser routes and 503 for
    # /api/ routes.  See GitHub issue #70.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'sethlans_manager.middleware.setup_gate.SetupGateMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# --- Waitress listener configuration ---
# See ``development/specs/waitress-migration-manager.md`` Phases 2 & 5.
# Phase 5 promoted Waitress to serve BOTH listeners (public-origin +
# internal-origin) in the same process, both fronted by Caddy.
# Settings surface eagerly so ``UrlconfOriginMiddleware`` can validate
# ``SERVER_PORT`` against a known set and fail closed on unknown ports.
# Full override hierarchy documented in :mod:`waitress_config`.
from .waitress_config import (  # noqa: E402
    resolve_internal_port_for_settings,
    resolve_public_port_for_settings,
)

WAITRESS_LOOPBACK_PORT_INTERNAL = resolve_internal_port_for_settings(_config)
WAITRESS_LOOPBACK_PORT_PUBLIC = resolve_public_port_for_settings(_config)

# Header-injection defense — prevent ``X-Forwarded-Port`` /
# ``X-Forwarded-Host`` from short-circuiting the port-based URLconf
# split enforced by ``UrlconfOriginMiddleware``.  Django's defaults are
# already ``False`` but we set them explicitly so a future deployment
# knob can't silently flip them on.
USE_X_FORWARDED_PORT = False
USE_X_FORWARDED_HOST = False

# Caddy terminates TLS in front of plaintext Waitress (Phase 5+).  Without
# this header Django reports ``request.is_secure() == False`` and builds
# ``http://`` absolute URLs for media/asset downloads, which the worker
# then fails to fetch against the HTTPS-only Caddy vhost.  Setting the
# header tells Django to trust Caddy's ``X-Forwarded-Proto: https`` when
# building URLs, CSRF origin, etc.
#
# Safety: Waitress binds 127.0.0.1 only, so no external client can reach
# it directly.  Caddy's ``reverse_proxy`` strips any incoming
# ``X-Forwarded-Proto`` header before setting its own, so an attacker
# cannot spoof ``https`` via the public vhost either.  The header is
# independent of the port-based URLconf split — that reads
# ``SERVER_PORT`` (WSGI environ), which ``X-Forwarded-*`` headers never
# touch.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

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


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# Hierarchy: env vars > manager.ini [database] > SQLite default
from .db_config import build_database_config  # noqa: E402
DATABASES = build_database_config(_config, _config_file_path)


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

_PW_VALIDATION = 'django.contrib.auth.password_validation'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': f'{_PW_VALIDATION}.UserAttributeSimilarityValidator'},
    {'NAME': f'{_PW_VALIDATION}.MinimumLengthValidator'},
    {'NAME': f'{_PW_VALIDATION}.CommonPasswordValidator'},
    {'NAME': f'{_PW_VALIDATION}.NumericPasswordValidator'},
]


# Internationalization — https://docs.djangoproject.com/en/5.2/topics/i18n/
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

# Dev ergonomics — rescan the static-file manifest on every request when
# DEBUG=True or SETHLANS_DEV_MODE=1 so edits to Angular bundles show up
# without a process restart. In production this stays off (the default)
# — whitenoise caches the manifest at process start for speed.
# See waitress-migration-manager spec, Phase 5 "Dev hot-reload".
_dev_mode_env = os.getenv('SETHLANS_DEV_MODE', '').lower() in ('1', 'true', 'yes')
WHITENOISE_AUTOREFRESH = bool(DEBUG or _dev_mode_env)

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Logging Configuration ---
# Disable Django's built-in logging bootstrap so it never tries to apply
# ``DEFAULT_LOGGING`` (which references ``django.utils.log.AdminEmailHandler``
# and the ``django.core.mail`` import chain).  In PyInstaller bundles that
# import chain isn't fully collected, which would otherwise crash startup
# with "Unable to configure handler 'mail_admins'".  We call
# ``sethlans_manager.logging_config.configure()`` ourselves after
# ``django.setup()`` — see that module for details.
LOGGING_CONFIG = None
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
