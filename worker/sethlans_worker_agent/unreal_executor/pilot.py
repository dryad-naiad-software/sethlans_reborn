# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pilot render, worker side (Unreal foundation FR-UE-25, D2).

Chunk 7a defines only the command-line constants the launch builder
needs (FR-UE-6, FR-UE-7, Dom R3-1); the pilot run, its metrics and the
``pilot-result/`` report arrive with the executor flow (chunk 7b).

``PILOT_CSV_CAPTURE_FRAMES`` is a frame count far above any pilot's
length, so the CSV capture covers the whole render and ends when UE
exits. It is a constant, never the attempt number ``<n>`` used in log
names (v4.3's ``<n>`` collided with it).
"""

PILOT_CSV_CAPTURE_FRAMES = 5000000
PILOT_CSV_FLAGS = (f"-csvCaptureFrames={PILOT_CSV_CAPTURE_FRAMES}", "-csvGpuStats")
