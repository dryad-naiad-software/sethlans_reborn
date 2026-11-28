# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/topology.py`` (issue #210).

The wizard writes ``manager`` / ``manager_worker`` / ``worker_only`` to
``topology.json``; the launcher and the tray must both map those (and
the canonical ``worker`` / ``manager+worker`` forms) the same way.
"""

from __future__ import annotations

import pytest

from shared import topology as topo
from wizard.sethlans_wizard.handlers.topology import VALID_TOPOLOGIES


class TestNormalizeTopology:

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("manager", topo.TOPOLOGY_MANAGER),
            ("worker", topo.TOPOLOGY_WORKER),
            ("manager+worker", topo.TOPOLOGY_BOTH),
            ("manager_worker", topo.TOPOLOGY_BOTH),
            ("worker_only", topo.TOPOLOGY_WORKER),
        ],
    )
    def test_known_spellings_map_to_canonical(self, raw, expected):
        assert topo.normalize_topology(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["", "universe", "WORKER_ONLY", " worker", None, 3, ["worker"]],
    )
    def test_unknown_or_non_string_returns_none(self, raw):
        assert topo.normalize_topology(raw) is None

    def test_every_wizard_spelling_is_recognized(self):
        """Contract with the wizard: no value it can write is unknown."""
        for value in VALID_TOPOLOGIES:
            assert topo.normalize_topology(value) is not None, value


class TestPredicates:

    @pytest.mark.parametrize(
        ("raw", "manager", "worker"),
        [
            ("manager", True, False),
            ("worker", False, True),
            ("worker_only", False, True),
            ("manager_worker", True, True),
            ("manager+worker", True, True),
            ("", False, False),
            ("universe", False, False),
            (None, False, False),
        ],
    )
    def test_has_manager_and_has_worker(self, raw, manager, worker):
        assert topo.has_manager(raw) is manager
        assert topo.has_worker(raw) is worker


class TestNoFailOpenHelper:

    def test_no_default_resolver_exported(self):
        """The fail-open ``resolve_topology`` helper was removed; each caller
        decides how to fail on an unknown topology."""
        assert not hasattr(topo, "resolve_topology")
