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

# Audio-only jobs only (sequence rendering FR-SA-4, R10): the farm tier of their CVars and their render flags.
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

# Every UE launch that can open a window (the warm-up editor session and the
# -game render, pilot included) runs headless on every platform, like Blender's
# -b: on Windows -RenderOffscreen selects the null platform application
# (WindowsPlatformApplicationMisc.cpp:173-176) and a swap-chain-less D3D12
# viewport (WindowsD3D12Viewport.cpp:34), while Slate still draws every window
# off screen (SlateApplication.cpp:1249, 1513). The prep commandlet creates no
# Slate application at all (LaunchEngineLoop.cpp:3108) and does not take it.
HEADLESS_FLAGS = ("-RenderOffscreen",)
HEADLESS_PHASES = frozenset({"warmup", "render"})

# Every UE launch: no UdpMessaging bridge (#233). The editor starts it by
# default (UdpMessagingModule.cpp:323-327) on UDP 0.0.0.0:6666 + an ephemeral
# port; -UDPMESSAGING_TRANSPORT_ENABLE=0 (:628) clears EnableTransport, so
# RestartServices (:340-351) never calls InitializeBridge. (TraceLog's TCP
# control listener on 0.0.0.0:1985 has no switch in 5.8.4; see the #233 notes.)
NETWORK_FLAGS = ("-UDPMESSAGING_TRANSPORT_ENABLE=0",)
