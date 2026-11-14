# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pure-Python Unreal validators shared by the manager and the worker
(Unreal foundation FR-MU-5).

Import the submodules directly (``builds``, ``unc``, ``share_paths``,
``validation``, ``vram_gate``, ``overlay_digest``); this package imports
nothing. Only ``overlay_digest`` reads the filesystem (the overlay tree it
is given); nothing here touches the network.
"""
