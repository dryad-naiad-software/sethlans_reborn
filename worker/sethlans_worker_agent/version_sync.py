# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender version synchronization between worker and manager.

Handles:
- Parsing required_blender_versions from heartbeat responses.
- Comparing required versions against locally installed versions.
- Downloading missing versions and upgrading patches.
- Cleaning up versions no longer required by the manager.
- Deferring downloads/cleanup when the worker is busy rendering.

Thread safety: download and cleanup operations are safe to call from
the main loop. They interact with ToolManager which tracks in-progress
downloads via _downloading_versions (protected by _download_lock).
"""

import logging
import threading
from typing import Dict, List, Set

from sethlans_worker_agent.tool_manager import tool_manager_instance

logger = logging.getLogger(__name__)

# Queued version downloads for when the worker is busy (P2-F6).
_pending_downloads: List[Dict[str, str]] = []
_pending_lock = threading.Lock()

# Queued version removals deferred because of active renders (P5-F6).
_pending_removals: List[str] = []
_pending_removals_lock = threading.Lock()


def parse_required_versions(heartbeat_response):
    """Extract the required_blender_versions list from a heartbeat response."""
    if not heartbeat_response:
        return []
    versions = heartbeat_response.get('required_blender_versions', [])
    if not isinstance(versions, list):
        logger.warning("Unexpected required_blender_versions format in heartbeat.")
        return []
    return versions


def get_installed_versions():
    """Return a dict mapping series -> full version for installed Blenders."""
    installed = {}
    for b in tool_manager_instance.scan_for_local_blenders():
        version = b['version']
        series = '.'.join(version.split('.')[:2])
        # Keep the highest patch per series if multiple are installed.
        if series not in installed or _version_tuple(version) > _version_tuple(installed[series]):
            installed[series] = version
    return installed


def _version_tuple(version_str):
    """Convert a version string to a comparable tuple of ints."""
    return tuple(int(p) for p in version_str.split('.'))


def compute_download_actions(required_versions, installed_versions):
    """Determine which versions need downloading or upgrading."""
    actions = []
    for req in required_versions:
        series = req.get('series', '')
        required_ver = req.get('version', '')
        if not series or not required_ver:
            continue

        installed_ver = installed_versions.get(series)
        if not installed_ver:
            # Missing entirely -- need to download (P2-F2).
            actions.append(req)
        elif _version_tuple(required_ver) > _version_tuple(installed_ver):
            # Newer patch available -- upgrade (P4-F2).
            actions.append(req)
    return actions


def compute_removable_versions(required_versions, installed_versions, active_jobs):
    """Determine which installed versions should be removed (P5-F6)."""
    required_series = {r.get('series', '') for r in required_versions}

    # Collect series in use by active jobs.
    in_use_series: Set[str] = set()
    for job_info in active_jobs.values():
        bv = job_info.get('blender_version', '')
        if bv:
            in_use_series.add('.'.join(bv.split('.')[:2]))

    removable = []
    deferred = []
    for series, version in installed_versions.items():
        if series not in required_series:
            if series in in_use_series:
                logger.info(
                    f"Deferring removal of {version} -- in use by active job"
                )
                deferred.append(version)
            else:
                removable.append(version)

    return removable, deferred


def download_versions(actions):
    """Download a list of required Blender versions. Returns success count."""
    success_count = 0
    for action in actions:
        version = action.get('version', '')
        if not version:
            continue
        logger.info(f"Downloading required Blender version {version}...")
        result = tool_manager_instance.ensure_blender_version_available(version)
        if result:
            success_count += 1
            logger.info(f"Successfully installed Blender {version}.")
        else:
            logger.error(f"Failed to download Blender {version}.")
    return success_count


def upgrade_patch_versions(actions, installed_versions):
    """Download new patches and remove old ones for upgraded series (P4-F2, P4-F4)."""
    success_count = 0
    for action in actions:
        series = action.get('series', '')
        new_version = action.get('version', '')
        old_version = installed_versions.get(series)

        if not old_version or not new_version:
            continue
        if _version_tuple(new_version) <= _version_tuple(old_version):
            continue

        logger.info(f"Upgrading {series}: {old_version} -> {new_version}")
        result = tool_manager_instance.ensure_blender_version_available(new_version)
        if result:
            # Verify new version is valid before removing old one.
            new_exe = tool_manager_instance.get_blender_executable_path(new_version)
            if new_exe:
                tool_manager_instance.remove_blender_version(old_version)
                success_count += 1
            else:
                logger.error(f"New version {new_version} not valid after download. Keeping {old_version}.")
        else:
            logger.error(f"Failed to download {new_version}. Keeping {old_version}.")
    return success_count


def remove_unrequired_versions(removable_versions):
    """Remove versions not in the manager's required list (P5-F6)."""
    for version in removable_versions:
        logger.info(f"Removing unrequired Blender version {version}.")
        tool_manager_instance.remove_blender_version(version)


def queue_pending_downloads(actions):
    """Queue downloads for when the worker finishes its current job (P2-F6)."""
    with _pending_lock:
        _pending_downloads.clear()
        _pending_downloads.extend(actions)
    if actions:
        logger.info(f"Queued {len(actions)} version download(s) for after current job.")


def queue_pending_removals(versions):
    """Queue removals for versions deferred due to active renders."""
    with _pending_removals_lock:
        for v in versions:
            if v not in _pending_removals:
                _pending_removals.append(v)


def process_pending_downloads():
    """Process any queued downloads. Call when the worker becomes idle."""
    with _pending_lock:
        actions = list(_pending_downloads)
        _pending_downloads.clear()
    if actions:
        logger.info(f"Processing {len(actions)} queued version download(s).")
        download_versions(actions)


def process_pending_removals(active_jobs):
    """Process any queued removals that are no longer in use."""
    with _pending_removals_lock:
        pending = list(_pending_removals)
        _pending_removals.clear()

    if not pending:
        return

    in_use_series: Set[str] = set()
    for job_info in active_jobs.values():
        bv = job_info.get('blender_version', '')
        if bv:
            in_use_series.add('.'.join(bv.split('.')[:2]))

    still_deferred = []
    for version in pending:
        series = '.'.join(version.split('.')[:2])
        if series in in_use_series:
            still_deferred.append(version)
        else:
            logger.info(f"Removing previously deferred version {version}.")
            tool_manager_instance.remove_blender_version(version)

    if still_deferred:
        with _pending_removals_lock:
            _pending_removals.extend(still_deferred)


def sync_versions(heartbeat_response, is_busy, active_jobs):
    """
    Sync installed versions against manager requirements on heartbeat.

    Returns True if at least one required version is installed.
    """
    required = parse_required_versions(heartbeat_response)
    if not required:
        logger.debug("No required_blender_versions in heartbeat response.")
        return True

    installed = get_installed_versions()
    actions = compute_download_actions(required, installed)

    # Separate new installs from patch upgrades.
    new_installs = [a for a in actions if a['series'] not in installed]
    upgrades = [a for a in actions if a['series'] in installed]

    if is_busy:
        if new_installs or upgrades:
            queue_pending_downloads(actions)
    else:
        if new_installs:
            download_versions(new_installs)
        if upgrades:
            # Re-read installed after new downloads.
            installed = get_installed_versions()
            upgrade_patch_versions(upgrades, installed)

    # Cleanup: remove versions not in required list (P5-F6).
    installed = get_installed_versions()
    removable, deferred = compute_removable_versions(required, installed, active_jobs)
    if is_busy:
        queue_pending_removals(removable + deferred)
    else:
        remove_unrequired_versions(removable)
        queue_pending_removals(deferred)

    # Process any previously deferred removals.
    if not is_busy:
        process_pending_removals(active_jobs)

    # Check if at least one required version is installed.
    installed = get_installed_versions()
    required_series = {r.get('series', '') for r in required}
    return any(s in installed for s in required_series)
