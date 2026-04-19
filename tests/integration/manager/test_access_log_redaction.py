# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Access-log redaction integration (FR-16c / NFR-4a).

Deferred — backend report flagged this as a follow-up item because
uvicorn access-log redaction requires server-level logging-filter
wiring that is not exercised by the Django test client.  This file
documents the requirement so the check is not lost.
"""

import pytest


@pytest.mark.skip(
    reason=(
        "Deferred in implementation phase — requires uvicorn-level "
        "access-log capture. Tracked in the setup-auth-unification "
        "backend report."
    ),
)
def test_setup_path_query_strings_redacted_in_access_log():
    """Asserts uvicorn access logs for /setup/* redact query strings.

    See spec FR-16c and NFR-4a.  Implementation will wire a logging
    filter into ``sethlans_manager/settings.py`` LOGGING and add an
    end-to-end capture test under ``tests/e2e/``.
    """
    raise NotImplementedError  # pragma: no cover
