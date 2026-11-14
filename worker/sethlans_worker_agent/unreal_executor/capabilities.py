# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal capabilities report and poll parameters (FR-WD-7, FR-WD-9, FR-WD-10).

Placeholder until ``config_unreal``, engine detection, the share state
and the GPU memory sampler land (spec chunk 4). Until then Unreal is
always "disabled by config": every function returns the FR-WD-7
short-circuit value and touches nothing (no detection, no share, no
cache directory, no ``nvidia-smi``; NF-6). The signatures are the ones
``unreal_bridge`` calls and chunk 4 keeps.
"""
from typing import Optional

_DISABLED_BY_CONFIG_REASON = "disabled by config"


def snapshot() -> dict:
    """Capabilities report for registration and heartbeats (FR-WD-7)."""
    return {
        "schema": 1,
        "enabled": False,
        "disabled_reason": _DISABLED_BY_CONFIG_REASON,
        "installs": [],
        "share": "",
        "share_reachable": False,
    }


def poll_params(node_idle: bool) -> dict:
    """Extra poll parameters asking for Unreal jobs (FR-WD-9).

    Empty while Unreal is disabled, so poll parameters stay
    byte-identical to a Blender-only worker's.
    """
    return {}


def gpu_memory_payload() -> Optional[dict]:
    """Heartbeat ``gpu_memory`` value (FR-RT-16); None omits the key."""
    return None
