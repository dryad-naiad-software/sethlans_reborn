# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the Unreal manager integration tests (spec FR-T-3).

Not a test module: imported by ``test_unreal_*.py``.
"""

from workers.models import Asset, ManagerSettings, Project

BUILD = '5.8.4-58902052'
SHARE = r'\\alpha.lan\mestrella\applications\unreal'
ARCHIVE_SHA = 'ab' * 32
OVERLAY_SHA = 'cd' * 32
RENDER_SETTINGS = {
    'unreal.map': '/Game/Maps/Main.Main',
    'unreal.sequence': '/Game/Cinematics/Edit.Edit',
    'unreal.preset': '/Game/Presets/Cine.Cine',
}


def set_share(value):
    """Store the manager's share directly (bypasses the API and its validation)."""
    ManagerSettings.objects.filter(pk=1).update(sethlans_share=value)


def caps(*, enabled=True, builds=(BUILD,), share=SHARE, reachable=True, supported=True):
    """An ``unreal_capabilities`` dict as the heartbeat validator stores it."""
    return {
        'schema': 1, 'enabled': enabled, 'disabled_reason': '',
        'installs': [{'build': b, 'supported': supported, 'source': 'launcher'} for b in builds],
        'share': share, 'share_reachable': reachable,
    }


def gpu_memory(*totals, used=0):
    """A stored ``gpu_memory`` dict with one GPU per total."""
    return {
        'schema': 1, 'source': 'nvidia-smi', 'age_s': 3,
        'gpus': [
            {'index': i, 'name': 'GPU', 'total_mb': t, 'used_mb': used,
             'sethlans_used_mb': None, 'other_used_mb': None}
            for i, t in enumerate(totals)
        ],
    }


def unreal_project(name='UnrealProj', **extra):
    return Project.objects.create(name=name, renderer='UNREAL', engine_build=BUILD, **extra)


def unreal_asset(project=None, name='UnrealAsset', **extra):
    project = project or unreal_project()
    fields = {
        'source_type': 'shared_project', 'archive_path': 'incoming/scene.zip',
        'uproject_path': 'Scene/Scene.uproject', 'archive_sha256': ARCHIVE_SHA,
    }
    fields.update(extra)
    return Asset.objects.create(name=name, project=project, **fields)
