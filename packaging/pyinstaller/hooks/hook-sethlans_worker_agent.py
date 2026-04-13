# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
PyInstaller hook for the sethlans_worker_agent package.

Ensures additional hidden imports are included when PyInstaller
freezes the worker. These modules are loaded dynamically at runtime.
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('sethlans_worker_agent')

# Explicit additions for modules with dynamic imports
hiddenimports += [
    'sethlans_worker_agent.agent',
    'sethlans_worker_agent.config',
    'sethlans_worker_agent.api_handler',
    'sethlans_worker_agent.job_processor',
    'sethlans_worker_agent.blender_executor',
    'sethlans_worker_agent.tool_manager',
    'sethlans_worker_agent.asset_manager',
    'sethlans_worker_agent.system_monitor',
    'sethlans_worker_agent.config_store',
    'sethlans_worker_agent.config_store.paths',
    'sethlans_worker_agent.config_store.io',
    'sethlans_worker_agent.web_ui',
    'sethlans_worker_agent.web_ui.server',
    'sethlans_worker_agent.web_ui.auth',
    'sethlans_worker_agent.web_ui.status',
]
