# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal Engine support for the worker (Unreal foundation, phase 1).

This package deliberately contains no imports (FR-WX-1, Dom N-5): the
rest of the worker reaches it only through ``unreal_bridge`` and the lazy
lookup in ``executors.get_executor``, so a broken Unreal module can never
stop Blender work.
"""
