# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``worker/sethlans_worker_agent/runtime_state.py``.

Covers FR-RT-2 (boot_id is a 32-char lowercase hex string set at module
load) and AC-5 (boot_id stability across same-process reads + uniqueness
across simulated process restarts via ``importlib.reload``).

The same-process stability assertion is the Conc LOW-2 strengthening
(per spec v2): proves the value is bound at import time rather than
regenerated per access, guarding against a future refactor that moves
the assignment into a function or property.
"""

from __future__ import annotations

import importlib
import re
import uuid

from sethlans_worker_agent import runtime_state


HEX_RE = re.compile(r"^[0-9a-f]{32}$")


class TestWorkerBootIdShape:
    """FR-RT-2: ``worker_boot_id`` is a 32-char lowercase hex string."""

    def test_boot_id_is_set(self):
        # Bound at module load -- never None after import.
        assert runtime_state.worker_boot_id is not None

    def test_boot_id_is_string(self):
        assert isinstance(runtime_state.worker_boot_id, str)

    def test_boot_id_is_32_char_lowercase_hex(self):
        boot_id = runtime_state.worker_boot_id
        assert len(boot_id) == 32
        assert HEX_RE.fullmatch(boot_id) is not None
        # Lowercase: rejecting any uppercase guards against future
        # refactors that swap to ``uuid.uuid4().hex.upper()``.
        assert boot_id == boot_id.lower()

    def test_boot_id_parses_as_uuid_hex(self):
        # Round-trip through uuid.UUID(hex=...) -- if uuid4().hex
        # ever changes shape, this catches it.
        parsed = uuid.UUID(hex=runtime_state.worker_boot_id)
        assert parsed.hex == runtime_state.worker_boot_id


class TestWorkerBootIdStability:
    """Conc LOW-2: ``boot_id`` is stable across reads in same process."""

    def test_two_consecutive_reads_match(self):
        # Proves import-time binding; no per-access regeneration.
        first = runtime_state.worker_boot_id
        second = runtime_state.worker_boot_id
        assert first == second

    def test_many_reads_match(self):
        # Stronger sanity check: reading N times still returns the
        # same value (no hidden lazy/cached side effect).
        baseline = runtime_state.worker_boot_id
        for _ in range(50):
            assert runtime_state.worker_boot_id == baseline


class TestWorkerBootIdReload:
    """FR-RT-2 / AC-5: simulated process restart yields a new boot_id."""

    def test_boot_id_differs_after_reload(self):
        # ``importlib.reload`` re-executes the module body, simulating
        # a fresh process start. The new uuid4 must differ.
        original = runtime_state.worker_boot_id
        try:
            reloaded = importlib.reload(runtime_state)
            assert reloaded.worker_boot_id != original
            # Reloaded value also satisfies the shape contract.
            assert HEX_RE.fullmatch(reloaded.worker_boot_id) is not None
        finally:
            # Restore the module so sibling tests see a stable value.
            importlib.reload(runtime_state)
