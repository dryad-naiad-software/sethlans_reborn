# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller hook for the sethlans_manager package.

Ensures additional hidden imports are included when PyInstaller
freezes the manager. These modules are discovered at runtime by
Django's app registry and are not captured by static analysis.
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('sethlans_manager')

# Django internals that the settings module references at runtime
hiddenimports += [
    'sethlans_manager.settings',
    'sethlans_manager.urls',
    'sethlans_manager.asgi',
    'sethlans_manager.wsgi',
    'sethlans_manager.logging_config',
    'sethlans_manager.drf_config',
    'sethlans_manager.cert_utils',
    'sethlans_manager.runtime_init',
    'sethlans_manager.runtime_state',
]
