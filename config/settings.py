#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

from pathlib import Path # <-- ADDED THIS IMPORT!
import os
import logging

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-^&r@p#+r6h*!@!1u=l!0j_z%z!%^n#b=2#h&l16b%c!0609t'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party apps
    'rest_framework',
    'django_filters',
    'pytest_django',
    # Your custom apps here
    'workers',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
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

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# Use environment variable for DB name, otherwise default to db.sqlite3
DB_NAME = os.getenv('SETHLANS_DB_NAME', BASE_DIR / 'db.sqlite3')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': DB_NAME, # Use the new variable here
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

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- NEW: Logging Configuration ---
# Get default log level from environment variable, fallback to INFO for production-like verbosity
LOG_LEVEL = os.getenv('DJANGO_LOG_LEVEL', 'INFO').upper()

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
            'formatter': 'standard', # Use our custom standard formatter
            'level': 'DEBUG', # Always output DEBUG to console handler, filtering happens at logger level
        },
        # You can add a file handler here if you want logs to go to a file
        # 'file': {
        #     'class': 'logging.handlers.RotatingFileHandler',
        #     'filename': BASE_DIR / 'logs' / 'django.log',
        #     'level': 'INFO',
        #     'formatter': 'standard',
        #     'maxBytes': 1024 * 1024 * 5, # 5 MB
        #     'backupCount': 5,
        # },
    },
    'loggers': {
        # Root logger: catches all messages not handled by specific loggers
        '': {
            'handlers': ['console'],
            'level': LOG_LEVEL, # Use the dynamic log level
            'propagate': True, # Pass messages to parent loggers (e.g., root)
        },
        # Your specific application logger
        'workers': { 
            'handlers': ['console'],
            'level': LOG_LEVEL, 
            'propagate': False, # Don't pass to root if handled here to avoid duplication
        },
        # Example for Django's built-in loggers
        'django': {
            'handlers': ['console'],
            'level': 'INFO', # Keep Django's own logs at INFO by default
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING', # Requests that return 4xx or 5xx will be logged as WARNING or ERROR
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'INFO', # Log SQL queries at DEBUG level, otherwise INFO
            'propagate': False,
        },
    },
}

# --- NEW: Media Files (User Uploads) Configuration ---
MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('SETHLANS_MEDIA_ROOT', os.path.join(BASE_DIR, 'media'))