# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Constants and gating of the real Unreal E2E (Unreal foundation FR-T-8).

The E2E is opt-in (``SETHLANS_UNREAL_E2E=1``) and also needs Windows, an
NVIDIA GPU, a supported UE 5.8.4 install and the PoC archive on the
Sethlans share. ``unreal_skip_reason`` checks them in that order, so a
CI runner (not opted in) stops at the first check without importing
worker code, running ``nvidia-smi`` or touching the network.

Optional overrides: ``SETHLANS_UNREAL_E2E_SHARE`` (the share root),
``SETHLANS_UNREAL_E2E_CACHE_DIR`` (the worker's Unreal cache, a new
space-free dir used only by this test) and
``SETHLANS_UNREAL_E2E_ARCHIVE_SHA256`` (skips hashing the 3.7 GB archive).
"""

import os
import platform
import subprocess
from pathlib import Path

OPT_IN_ENV = "SETHLANS_UNREAL_E2E"
SHARE_ENV = "SETHLANS_UNREAL_E2E_SHARE"
CACHE_DIR_ENV = "SETHLANS_UNREAL_E2E_CACHE_DIR"
ARCHIVE_SHA256_ENV = "SETHLANS_UNREAL_E2E_ARCHIVE_SHA256"

SHARE = os.environ.get(SHARE_ENV) or r"\\alpha\mestrella\applications\unreal"
# Never an artist's cache or a spike/smoke dir.
DEFAULT_CACHE_DIR = r"D:\sethlans-uecache-e2e"

# The PoC (development/specs/unreal-spike-results.md, unreal-chunk7b-smoke.md).
ENGINE_BUILD = "5.8.4-58902052"
ARCHIVE_RELPATH = "incoming/ACOMAnimationSample.zip"
UPROJECT_RELPATH = "ACOMAnimationSample/ACOMAnimationSample.uproject"
OVERLAY_RELPATH = "project-fixes"
CVAR_OVERRIDES = {
    "Constraints.NewEvaluation": "1",
    "r.Streaming.PoolSize": "9000",
    "r.RayTracing.ResidentGeometryMemoryPoolSizeInMB": "2048",
}
RENDER_SETTINGS = {
    "unreal.map": "/Game/AnimationSample/Episodes/02_intro_beta/Intro_Beta_Master_lvl",
    "unreal.sequence": "/Game/AnimationSample/Episodes/02_intro_beta/Intro_Beta_Edit",
    "unreal.preset": "/Game/AnimationSample/Presets/MRG_Beta_Edit_HD",
    "unreal.output_format": "PNG",
}
FRAME = 1000


def archive_on_share():
    """The PoC archive's full path on the share (only ever read)."""
    return Path(SHARE, *ARCHIVE_RELPATH.split("/"))


def _opted_in():
    return os.environ.get(OPT_IN_ENV, "").strip().lower() in ("1", "true")


def _nvidia_gpu_present():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _engine_installed():
    # Imported lazily: only an opted-in Windows node gets this far.
    from sethlans_worker_agent.unreal_executor.detection import detect_engines
    installs = detect_engines(
        engine_paths=os.environ.get("SETHLANS_UNREAL_ENGINE_PATHS", ""),
    )
    return any(i.supported and i.build == ENGINE_BUILD for i in installs)


def unreal_skip_reason():
    """Why the real Unreal E2E cannot run here, or ``None``."""
    if not _opted_in():
        return f"real Unreal E2E is opt-in: set {OPT_IN_ENV}=1"
    if platform.system() != "Windows":
        return "Unreal rendering is Windows-only in phase 1"
    if not _nvidia_gpu_present():
        return "no NVIDIA GPU (nvidia-smi reports none)"
    if not _engine_installed():
        return f"Unreal Engine {ENGINE_BUILD} is not installed"
    if not archive_on_share().is_file():
        return f"the PoC archive is not reachable: {archive_on_share()}"
    return None
