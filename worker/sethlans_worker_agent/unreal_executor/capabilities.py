# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal capabilities report, poll parameters and GPU memory payload
(Unreal foundation FR-WD-7, FR-WD-8, FR-WD-9, FR-WD-10).

Reached only through ``unreal_bridge``. Every function checks
``config_unreal.ENABLED`` **first**: when it is false they return the
"disabled by config" report, ``{}`` and ``None`` without running
detection, touching ``CACHE_DIR`` or the share, creating directories,
taking the owner lock, calling an ACL API or running ``nvidia-smi``
(NF-6). Otherwise ``runtime.startup()`` runs once (idempotent).

``share`` and ``share_reachable`` come from **one** ``unreal_share.state()``
read, so they are never torn. Share reachability does not affect
``enabled``; it only gates the poll parameters (and the manager's claim).
"""
from typing import List, Optional

from sethlans_worker_agent import config_unreal, unreal_share
from sethlans_worker_agent.unreal_executor import runtime, vram

SCHEMA = 1
DISABLED_BY_CONFIG_REASON = "disabled by config"
MAX_REASON_LENGTH = 200
MAX_REPORTED_INSTALLS = 16
MAX_POLL_BUILDS = 10
RENDERERS_WITH_UNREAL = 'BLENDER,UNREAL'


def _disabled_by_config() -> dict:
    return {
        "schema": SCHEMA,
        "enabled": False,
        "disabled_reason": DISABLED_BY_CONFIG_REASON,
        "installs": [],
        "share": "",
        "share_reachable": False,
    }


def startup() -> None:
    """Run the Unreal startup checks now (agent startup hook). No-op when disabled."""
    if config_unreal.ENABLED:
        runtime.startup()


def snapshot() -> dict:
    """Capabilities report for registration and heartbeats (FR-WD-7)."""
    if not config_unreal.ENABLED:
        return _disabled_by_config()
    runtime.startup()
    current = runtime.installs()
    enabled, reason = runtime.status(current)
    share = unreal_share.state()
    installs = [
        {"build": install.build, "supported": install.supported, "source": install.source}
        for install in current if install.build is not None
    ][:MAX_REPORTED_INSTALLS]
    return {
        "schema": SCHEMA,
        "enabled": enabled,
        "disabled_reason": reason[:MAX_REASON_LENGTH],
        "installs": installs,
        "share": share.root.canonical if share.root is not None else "",
        "share_reachable": bool(share.root is not None and share.reachable),
    }


def supported_builds() -> List[str]:
    """Sorted, de-duplicated builds of the supported installs."""
    return sorted({install.build for install in runtime.installs() if install.supported})


def poll_params(node_idle: bool) -> dict:
    """Extra poll parameters asking for Unreal jobs (FR-WD-9).

    Non-empty only when Unreal is enabled with a supported install, this
    worker's configured share is reachable, and the machine is fully idle
    (FR-WX-13). Otherwise ``{}``: the poll parameters stay byte-identical
    to a Blender-only worker's. Never adds an ``ordering`` key.
    """
    if not config_unreal.ENABLED or not node_idle:
        return {}
    runtime.startup()
    current = runtime.installs()
    enabled, _reason = runtime.status(current)
    if not enabled:
        return {}
    share = unreal_share.state()
    if share.root is None or not share.reachable:
        return {}
    builds = sorted({install.build for install in current if install.supported})[:MAX_POLL_BUILDS]
    if not builds:
        return {}
    return {'renderers': RENDERERS_WITH_UNREAL, 'unreal_builds': ','.join(builds)}


def gpu_memory_payload() -> Optional[dict]:
    """Heartbeat ``gpu_memory`` (FR-RT-16); None omits the key.

    None while Unreal is disabled by config (NF-6), when the sampler is
    not running (Unreal disabled at startup), or when there is no
    reading yet or the last sample failed.
    """
    if not config_unreal.ENABLED:
        return None
    runtime.startup()
    sampler = runtime.sampler()
    if sampler is None:
        return None
    return vram.to_payload(sampler.latest())
