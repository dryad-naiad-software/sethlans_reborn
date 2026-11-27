# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the version guard in
``worker/sethlans_worker_agent/multicast_listener.py``.

An announcement whose ``v`` is not the supported version is dropped and
warned about once per ``(manager_id, v)`` pair (FR-34). The id and version
come from the network, so the warning is escaped and cut and the warn-once
set is capped (FR-SEC-9).
"""

import pytest

from sethlans_worker_agent import multicast_listener as ml
from tests.unit.worker._multicast import encode as _encode, good_payload as _good_payload

pytestmark = pytest.mark.usefixtures("multicast_state")


class TestVersionGuard:
    @pytest.mark.parametrize("bad_v", [0, 2, 99, "1", "invalid"])
    def test_bad_version_dropped(self, bad_v):
        """FR-34: an announcement with any ``v`` other than the integer 1 is dropped."""
        seen = {}
        ml.MulticastListener._parse_into(
            _encode(_good_payload(v=bad_v)), seen,
        )
        assert seen == {}

    def test_warning_logged_once_per_pair(self, mocker):
        """FR-34: same (manager_id, v) pair -> warning fires exactly once."""
        warning_spy = mocker.patch.object(ml.logger, "warning")
        seen = {}
        bad = _good_payload(v=2)
        ml.MulticastListener._parse_into(_encode(bad), seen)
        ml.MulticastListener._parse_into(_encode(bad), seen)
        ml.MulticastListener._parse_into(_encode(bad), seen)
        assert warning_spy.call_count == 1

    def test_different_pairs_warn_separately(self, mocker):
        """FR-34: each new (manager_id, v) pair gets its own warning."""
        warning_spy = mocker.patch.object(ml.logger, "warning")
        seen = {}
        ml.MulticastListener._parse_into(
            _encode(_good_payload(v=2)), seen,
        )
        ml.MulticastListener._parse_into(
            _encode(_good_payload(
                v=3,
                manager_id="00000000-0000-0000-0000-000000000002",
            )), seen,
        )
        assert warning_spy.call_count == 2

    def test_warning_escapes_newlines_and_is_cut(self, mocker):
        """FR-SEC-9 [security review N3]: the id and version come from the network, so the warning shows them
        as ``repr`` (no raw newline to forge log lines) cut to 80 characters each."""
        warning_spy = mocker.patch.object(ml.logger, "warning")
        manager_id, v = "evil\nINFO forged " + "x" * 200, "2\n" + "9" * 200
        ml.MulticastListener._parse_into(_encode(_good_payload(manager_id=manager_id, v=v)), {})
        args = warning_spy.call_args.args
        message = args[0] % args[1:]
        assert "\n" not in message
        assert repr(manager_id)[:80] in message and repr(manager_id)[:81] not in message
        assert repr(v)[:80] in message and repr(v)[:81] not in message

    def test_warned_set_is_capped_at_64(self, mocker):
        """FR-SEC-9 [security review N3]: made-up (id, version) pairs cannot grow the warn-once set without
        bound."""
        warning_spy = mocker.patch.object(ml.logger, "warning")
        for n in range(ml.MAX_WARNED + 1):
            ml.MulticastListener._parse_into(_encode(_good_payload(manager_id=f"id-{n}", v=2)), {})
        assert ml.MAX_WARNED == 64
        assert len(ml._warned_versions) == 64 and warning_spy.call_count == 64
