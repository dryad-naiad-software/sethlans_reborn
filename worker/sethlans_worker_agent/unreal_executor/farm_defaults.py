# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Farm defaults for Unreal launches (Unreal foundation FR-UE-4, D2).

Data only. ``FARM_DEFAULTS_VERSION`` is bumped whenever a value here
changes the rendered result, which invalidates every warm-up marker
(FR-UE-9).

``Constraints.NewEvaluation=1`` and the two pool CVars are A-COM project
settings, not farm defaults: ``-DPCVars`` applies at ``SetByDeviceProfile``
priority, above a project's own ``DefaultEngine.ini [SystemSettings]``, so
a farm value would override other projects' own settings (D2).
"""

FARM_DEFAULTS_VERSION = 2

# D2: MRQ advances game time by 1/(fps x temporal samples).
FARM_CVARS = {"gc.TimeBetweenPurgingPendingKillObjects": "4"}
# The top precedence tier (FR-UE-5); empty in phase 1.
FARM_CRITICAL_CVARS: dict = {}

# Unused in phase 1 except by spike S5 (audio, phase 3).
AUDIO_CVARS = {"au.NeverMuteNonRealtimeAudioDevices": "1", "au.NeverDisableSubmixes": "1"}
AUDIO_FLAGS = ["-deterministicaudio", "-audiomixer", "-AudioCallbackBufferFrameSize=32",
               "-AudioNumBuffersToEnqueue=2"]

# bench.ps1's ``-ini:Input:`` token, verbatim (keeps a farm render from
# capturing or locking the mouse on a shared workstation).
INI_INPUT_OVERRIDE = ("-ini:Input:[/Script/Engine.InputSettings]:bCaptureMouseOnLaunch=False,"
                      "[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,"
                      "[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock")

RENDER_FLAGS = ("-windowed", "-resx=1280", "-resy=720", "-unattended", "-nosplash",
                "-NoLoadingScreen", "-log", INI_INPUT_OVERRIDE)

# Phase 4 (Linux); unused on Windows.
HEADLESS_LINUX_FLAGS = ("-RenderOffscreen",)
