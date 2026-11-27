# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The exit codes of ``run_manager`` that the launcher acts on.

Django-free and stdlib-only, so setup mode, the running manager and the
launcher can all import them.

* 10: setup mode saved the settings file; start the chosen mode.
* 11: the first start failed (the database could not be migrated, or
  ``[first_admin]`` or the ``SETHLANS_ADMIN_*`` variables are not usable,
  or the local worker could not be trusted).
* 12: there is no usable admin account after the first-start step.
* 13: the configured data folder cannot be used.

After 11 or 12 the launcher goes back to setup with the reason.
"""

EXIT_SETUP_SAVED = 10
EXIT_FIRST_START_FAILED = 11
EXIT_NO_ADMIN = 12
EXIT_DATA_FOLDER = 13
