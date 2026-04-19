# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``workers.permissions.IsSetupPhaseUser``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from workers.permissions import IsSetupPhaseUser


def _build_request(session_data=None, snapshot=None):
    request = MagicMock()
    request.user = MagicMock(is_authenticated=False)
    session = {} if session_data is None else dict(session_data)
    # Back MagicMock's session with dict semantics for .get()
    mock_session = MagicMock()
    mock_session.get = MagicMock(
        side_effect=lambda k, default=None: session.get(k, default),
    )
    request.session = mock_session
    request._setup_snapshot = snapshot or {
        "complete": False, "phase": "topology", "session_id": None,
    }
    return request


class TestIsSetupPhaseUser:

    def test_missing_session_denies(self):
        request = MagicMock()
        request.session = None
        assert IsSetupPhaseUser().has_permission(request, MagicMock()) is False

    def test_missing_setup_phase_flag_denies(self):
        request = _build_request(session_data={})
        assert IsSetupPhaseUser().has_permission(request, MagicMock()) is False

    def test_setup_phase_false_denies(self):
        request = _build_request(session_data={"setup_phase": False})
        assert IsSetupPhaseUser().has_permission(request, MagicMock()) is False

    def test_setup_phase_true_no_binding_allows(self):
        request = _build_request(
            session_data={
                "setup_phase": True, "setup_session_id": "abc",
            },
            snapshot={
                "complete": False, "phase": "topology", "session_id": None,
            },
        )
        assert IsSetupPhaseUser().has_permission(request, MagicMock()) is True

    def test_session_id_matches_binding_allows(self):
        request = _build_request(
            session_data={
                "setup_phase": True, "setup_session_id": "sid-1",
            },
            snapshot={
                "complete": False, "phase": "topology",
                "session_id": "sid-1",
            },
        )
        assert IsSetupPhaseUser().has_permission(request, MagicMock()) is True

    def test_session_id_mismatch_still_allows_permission(self):
        """Binding mismatch is no longer checked by the permission class.

        The single-writer binding check moved into
        ``workers.services.setup_session.enforce_setup_session_binding``
        so the error can surface as 409 ``setup_session_conflict``
        rather than the generic 403 that a False return would produce.
        """
        request = _build_request(
            session_data={
                "setup_phase": True, "setup_session_id": "sid-1",
            },
            snapshot={
                "complete": False, "phase": "topology",
                "session_id": "sid-other",
            },
        )
        assert IsSetupPhaseUser().has_permission(request, MagicMock()) is True

    def test_anonymous_user_allowed_when_flags_set(self):
        """``request.user`` is AnonymousUser during setup — D1."""
        request = _build_request(
            session_data={
                "setup_phase": True, "setup_session_id": "x",
            },
        )
        request.user = MagicMock(is_authenticated=False)
        assert IsSetupPhaseUser().has_permission(request, MagicMock()) is True
