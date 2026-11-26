# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Making a symbolic link or a junction for the Blender cache tests; skips when this machine cannot."""
import os

import pytest


def make_link(kind, link, target):
    """A link at ``link`` to ``target``; skip when this machine cannot make one."""
    try:
        if kind == 'junction':
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, ImportError, AttributeError) as exc:
        pytest.skip(f"no {kind} rights here: {exc}")
