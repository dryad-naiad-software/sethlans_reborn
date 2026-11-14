# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pure-Python Unreal validators shared by the manager and the worker
(Unreal foundation FR-MU-5).

Nothing here touches the filesystem or the network. Import the
submodules directly (``shared.unreal.builds``, ``shared.unreal.unc``);
this package imports nothing.
"""
