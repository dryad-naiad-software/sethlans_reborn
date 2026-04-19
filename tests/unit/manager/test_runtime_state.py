# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/sethlans_manager/runtime_state.py``.
"""

from __future__ import annotations

import importlib
import re
import uuid


UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


class TestManagerBootId:

    def test_boot_id_is_uuid4_hex(self):
        from sethlans_manager import runtime_state
        assert runtime_state.manager_boot_id is not None
        assert UUID_HEX_RE.match(runtime_state.manager_boot_id)
        # Parse back to a UUID to confirm validity.
        parsed = uuid.UUID(runtime_state.manager_boot_id)
        assert parsed.version == 4

    def test_reload_rotates_boot_id(self):
        from sethlans_manager import runtime_state
        first = runtime_state.manager_boot_id
        importlib.reload(runtime_state)
        second = runtime_state.manager_boot_id
        assert first != second
        assert UUID_HEX_RE.match(second)

    def test_initial_cert_fingerprint_is_none(self):
        """Other fields start as None until initialize_runtime_state runs."""
        import importlib
        from sethlans_manager import runtime_state
        importlib.reload(runtime_state)
        assert runtime_state.cert_fingerprint is None
        assert runtime_state.manager_id is None
