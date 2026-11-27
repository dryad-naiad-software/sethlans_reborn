# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``sethlans_manager.error_text.short_error``: the one-line
exit-11 reason of a failed migrate, collectstatic or first start
(FR-FS-0), which never carries a database's ``DETAIL:`` text.
"""
import pytest
from django.db import IntegrityError, OperationalError

from sethlans_manager.error_text import short_error


@pytest.mark.parametrize("error, text", [
    pytest.param(IntegrityError('duplicate key value violates unique constraint "auth_user_username_key"\n'
                                "DETAIL:  Key (username)=(farmadmin) already exists."),
                 'IntegrityError: duplicate key value violates unique constraint "auth_user_username_key"',
                 id="postgres-detail-line"),
    pytest.param(OperationalError("could not connect DETAIL: host=db user=farmadmin"),
                 "OperationalError: could not connect", id="detail-on-the-first-line"),
    pytest.param(OperationalError("first line\nsecond line"), "OperationalError: first line", id="first-line-only"),
    pytest.param(OperationalError("DETAIL: Key (username)=(farmadmin)"), "OperationalError", id="only-detail"),
    pytest.param(OperationalError(), "OperationalError", id="no-message"),
])
def test_short_error(error, text):
    """FR-FS-0: the class name and only the first line of the message, cut before DETAIL:."""
    assert short_error(error) == text
