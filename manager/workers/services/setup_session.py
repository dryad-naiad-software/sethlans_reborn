# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-session binding enforcement (FR-4a / C3).

Every mutating ``/api/setup/*`` request must carry the session whose
``setup_session_id`` matches the value bound in ``manager.ini [setup]
session_id``.  A second browser tab that bootstrap-races the first will
still receive a ``204`` from bootstrap but is rejected with 409
``setup_session_conflict`` on the very first mutation.

The permission class :class:`workers.permissions.IsSetupPhaseUser` only
answers "is this a setup-phase session at all?".  The session-id
binding check lives here and is called explicitly at the top of each
mutating view via :func:`enforce_setup_session_binding`, which raises
:class:`workers.services.setup_phase.SetupPhaseError` on mismatch --
caught by the unified DRF exception handler and rendered into the
envelope with ``code=setup_session_conflict`` / ``status=409``.
"""

from __future__ import annotations

from workers.services.setup_phase import (
    SetupPhaseError,
    setup_state_snapshot,
)


def enforce_setup_session_binding(request) -> None:
    """Raise if the request's setup session does not match the bound id.

    Called at the top of every mutating setup view.  Safe to call on
    any request -- it is a no-op if no binding is present in
    ``manager.ini`` yet (fresh wizard), so the first bootstrap-then-
    mutate pair from a single tab always succeeds.
    """
    session = getattr(request, "session", None)
    if session is None:
        return

    snapshot = setup_state_snapshot(request)
    bound = snapshot.get("session_id")
    if bound is None:
        return  # Nothing bound yet -- first-tab path.

    stored = session.get("setup_session_id")
    if stored == bound:
        return

    raise SetupPhaseError(
        code="setup_session_conflict",
        message=(
            "Another browser tab already owns the setup session. "
            "Close this tab and continue the wizard in the original tab."
        ),
        status=409,
        details={},
    )
