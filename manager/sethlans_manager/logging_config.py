# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging.config
import os
from pathlib import Path

from shared.frozen_paths import get_data_dir, is_frozen

# BASE_DIR must match settings.py
BASE_DIR = Path(__file__).resolve().parent.parent

# Get default log level from environment variable,
# fallback to INFO for production-like verbosity
LOG_LEVEL = os.getenv('DJANGO_LOG_LEVEL', 'INFO').upper()

# Create logs directory if it doesn't exist.
# In frozen mode on Windows, BASE_DIR is inside C:\Program Files\ (read-only).
# Write logs to the per-user data directory instead.
if is_frozen():
    LOGS_DIR = get_data_dir('manager') / 'logs'
else:
    LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,  # Keep Django's default loggers

    'formatters': {
        'verbose': {
            'format': (
                '{levelname} {asctime} {module} '
                '{process:d} {thread:d} {message}'
            ),
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
        'standard': {  # Custom format for standard application logs
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
            'maxBytes': 1024 * 1024 * 5,  # 5 MB
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
        'django.server': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}


def configure():
    """Apply the ``LOGGING`` dict via ``logging.config.dictConfig``.

    This function replaces Django's built-in logging bootstrap.
    Django's ``configure_logging()`` unconditionally applies
    ``DEFAULT_LOGGING`` (which includes a ``mail_admins`` handler using
    ``django.utils.log.AdminEmailHandler``) before the user-level
    ``LOGGING`` dict.  In a PyInstaller bundle, the ``AdminEmailHandler``
    import chain (``django.core.mail`` -> ``email.mime.*`` / ``smtplib``)
    isn't fully collected, so the handler class fails to resolve and
    ``dictConfig(DEFAULT_LOGGING)`` bails out with
    "Unable to configure handler 'mail_admins'".

    To avoid this, ``settings.LOGGING_CONFIG`` is set to ``None`` so
    Django skips its bootstrap, and callers invoke ``configure()``
    explicitly after ``django.setup()``.

    Idempotent — calling this multiple times simply re-applies the same
    configuration via ``dictConfig``, which replaces existing handlers
    on the affected loggers.
    """
    logging.config.dictConfig(LOGGING)
