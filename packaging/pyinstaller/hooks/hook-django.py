# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller runtime hook for Django.

Sets DJANGO_SETTINGS_MODULE before any Django import. Does NOT call
django.setup() — that is deferred to the explicit boot sequence in
run_manager.py (FR-8) to ensure data-dir paths are resolved first.
"""

import os

os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE', 'sethlans_manager.settings'
)
