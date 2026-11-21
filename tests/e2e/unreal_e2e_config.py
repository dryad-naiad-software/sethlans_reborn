# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Constants and gating of the real Unreal E2E (Unreal foundation FR-T-8).

The E2E is opt-in (``SETHLANS_UNREAL_E2E=1``) and also needs Windows, the
Sethlans share root in ``SETHLANS_UNREAL_E2E_SHARE``, a graphics card, a
supported UE 5.8.4 install and the PoC archive on that share.
``unreal_skip_reason`` checks them in that order, so a CI runner (not
opted in) stops at the first check without importing worker code,
probing the graphics cards or touching the network.

Required when opted in: ``SETHLANS_UNREAL_E2E_SHARE`` (the share root,
for example ``\\\\fileserver\\projects\\unreal``). Optional overrides:
``SETHLANS_UNREAL_E2E_CACHE_DIR`` (the worker's Unreal cache, a new
space-free dir used only by this test) and
``SETHLANS_UNREAL_E2E_ARCHIVE_SHA256`` (skips hashing the 3.7 GB archive).
"""

import os
import platform
import tempfile
from pathlib import Path

OPT_IN_ENV = "SETHLANS_UNREAL_E2E"
SHARE_ENV = "SETHLANS_UNREAL_E2E_SHARE"
CACHE_DIR_ENV = "SETHLANS_UNREAL_E2E_CACHE_DIR"
ARCHIVE_SHA256_ENV = "SETHLANS_UNREAL_E2E_ARCHIVE_SHA256"

# No built-in default: the share comes only from the environment.
SHARE = os.environ.get(SHARE_ENV, "").strip()
# Never an artist's cache or a spike/smoke dir. The worker's cache-dir
# check needs a space-free path with no 8.3 short name (``~``), so the
# temp dir is resolved to its long form; set CACHE_DIR_ENV when it still
# has a space.
DEFAULT_CACHE_DIR = os.path.join(os.path.realpath(tempfile.gettempdir()), "sethlans-uecache-e2e")

# The proof-of-concept project (an Unreal sample project) and its build.
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
# Below this the pilot's peak for the PoC is a broken measurement; the
# upper bound is the measured card's own memory (``peak_upper_bound_mb``).
MIN_PILOT_PEAK_MB = 6000

# Sequence E2E (sequence rendering chunk 9). The PoC sequence runs at 24/1,
# frames 0-1928, 23 shot-track shots; the two chosen shots are adjacent.
# With piece_seconds 5 (T = 120, limit 180) and the default 1 s lead-in
# (24 frames) the planner gives three pieces: the 212-frame shot splits in
# two (a boundary inside a shot) and the 134-frame shot is the third (a
# boundary at a cut). Every piece starts above frame 0, so each has a lead-in.
SEQUENCE_FRAMES = (0, 1928)
SEQUENCE_RATE = (24, 1)
SEQUENCE_SHOT_COUNT = 23
SEQUENCE_SHOTS = {"02_010_0055_Seq": (518, 730), "02_010_0060_Seq": (730, 864)}  # end exclusive
PIECE_SECONDS = 5
LEAD_IN_MS = 1000
# (start, end inclusive, lead-in) of each piece, in edit order.
SEQUENCE_PIECES = [(518, 623, 24), (624, 729, 24), (730, 863, 24)]
# The audio job renders the run from r0 = 518 - 24 (FR-SP-5).
SEQUENCE_AUDIO = (518, 863, 24)
# First frames of the chosen shots and of shot 02_010_0052 (448-518), which
# the first piece's lead-in and the audio lead-in reach into (OQ-2 (b)).
SEQUENCE_PILOT_FRAMES = [448, 518, 730]
SEQUENCE_SIZE = (640, 360)
SEQUENCE_VIDEO = {"preset": "web_h264"}
# U-9 on this sequence and preset: the sound leads the picture by 9,418
# samples at 48 kHz (AC-50 tolerance).
SOUND_OFFSET_SAMPLES = 9418
SOUND_OFFSET_TOLERANCE = 32
# FR-SA-5: the run's .wav is within 256 samples of its frames' length.
WAV_LENGTH_TOLERANCE = 256


def peak_upper_bound_mb(gpus, gpu_name, gpu_total_mb):
    """The measured card's total memory in MB, or ``None`` when unknown.

    ``gpus`` is the worker's heartbeat ``gpu_memory.gpus`` list; the card is
    the entry named ``gpu_name`` (the measurement's card). Cards with that
    name but different totals are ambiguous, so the measurement's own
    ``gpu_total_mb`` is used then, and when no entry matches.
    """
    totals = {
        gpu.get("total_mb") for gpu in gpus or []
        if gpu.get("name") == gpu_name and gpu.get("total_mb")
    }
    return totals.pop() if len(totals) == 1 else gpu_total_mb


def archive_on_share():
    """The PoC archive's full path on the share (only ever read)."""
    return Path(SHARE, *ARCHIVE_RELPATH.split("/"))


def _opted_in():
    return os.environ.get(OPT_IN_ENV, "").strip().lower() in ("1", "true")


def _graphics_card_present():
    # Imported lazily: only an opted-in Windows node gets this far. The
    # worker's own vendor-neutral probe; None means the probe failed.
    from sethlans_worker_agent.cards.os_probe import probe
    cards = probe()
    return bool(cards) and any(card.key for card in cards)


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
    if not SHARE:
        return f"no Sethlans share: set {SHARE_ENV} to the share root"
    if not _graphics_card_present():
        return "no graphics card found"
    if not _engine_installed():
        return f"Unreal Engine {ENGINE_BUILD} is not installed"
    if not archive_on_share().is_file():
        return f"the PoC archive is not reachable: {archive_on_share()}"
    return None
