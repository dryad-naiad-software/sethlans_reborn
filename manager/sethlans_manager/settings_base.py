# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Settings shared by the running manager (``settings``) and setup mode
(``settings_setup``): password validators and hashers, time and language,
and the logging switch. Nothing here touches a database, a data folder or
the ``workers`` app, so setup mode can load it.

``PASSWORD_HASHERS`` is left at Django's default in both modules, so a
hash made in setup mode is checked the same way by the running manager.
"""
# The MySQL driver goes in before Django can load a MySQL backend.
from sethlans_manager import mysql_driver  # noqa: F401

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

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django's own logging bootstrap stays off: each settings module brings
# its LOGGING dict and the process calls ``dictConfig`` itself (see
# ``logging_config.configure`` and ``setup_mode.logging_setup``).
LOGGING_CONFIG = None
