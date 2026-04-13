# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Backward-compatibility shim for manager cert_utils imports.

The implementation has been extracted to ``shared/cert_utils.py`` so
both the manager and the worker agent can share it. All existing
``from sethlans_manager.cert_utils import ...`` statements continue
to work through these re-exports.

The shim binds attributes directly from the ``shared.cert_utils``
module so that ``mocker.patch('sethlans_manager.cert_utils.X', ...)``
patches the same object that ``shared.cert_utils`` calls at runtime.
"""

import sys

import shared.cert_utils as _impl

# Replace this module in sys.modules with the shared implementation.
# This ensures that *all* attribute access (including
# mocker.patch targets like 'sethlans_manager.cert_utils.socket')
# resolves against the real module where functions are defined.
sys.modules[__name__] = _impl
