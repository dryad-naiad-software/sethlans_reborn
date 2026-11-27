# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``enrollment_key_notice_view`` on its own (setup-rebuild FR-WZ-9).

The ``ManagerSettings`` row and the transaction are mocks; the real row, the
permission class and a key rotation are checked in
``tests/integration/manager/test_enrollment_key_notice_integration.py``.
"""
import contextlib
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from workers.views import enrollment_key_notice as notice

KEY = "4F9XK2PBQ7M3N8RT"
DISPLAY = "4F9X-K2PB-Q7M3-N8RT"
HIDDEN = {"show": False, "enrollment_key": None}


@pytest.fixture
def row(monkeypatch):
    """The singleton row, read through either query the view makes."""
    found = MagicMock(enrollment_key=KEY, enrollment_key_notice_dismissed=False)
    model = MagicMock()
    model.objects.only.return_value.get.return_value = found
    model.objects.select_for_update.return_value.get.return_value = found
    monkeypatch.setattr(notice, "ManagerSettings", model)
    monkeypatch.setattr(notice, "transaction", SimpleNamespace(atomic=contextlib.nullcontext))
    return found


@pytest.fixture
def log(caplog):
    """The view's log records (the ``workers`` logger does not propagate to the root logger)."""
    notice.logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger=notice.logger.name)
    yield caplog
    notice.logger.removeHandler(caplog.handler)


def _call(method, caplog):
    request = getattr(APIRequestFactory(), method)("/api/auth/enrollment_key_notice/")
    # A plain object, not a MagicMock: ``IsAdminNotWorker`` refuses any user with ``worker_profile``.
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_staff=True, username="boss"))
    response = notice.enrollment_key_notice_view(request)
    assert response.status_code == 200
    assert KEY not in caplog.text and DISPLAY not in caplog.text
    return response.data


@pytest.mark.parametrize("dismissed, expected", [
    (False, {"show": True, "enrollment_key": DISPLAY}),
    (True, HIDDEN),
])
def test_get_shows_the_key_until_dismissed(row, log, dismissed, expected):
    """FR-WZ-9: the key, in display form, only while the notice is shown; never logged."""
    row.enrollment_key_notice_dismissed = dismissed
    assert _call("get", log) == expected


@pytest.mark.parametrize("dismissed, saves", [(False, 1), (True, 0)])
def test_post_dismisses_once(row, log, dismissed, saves):
    """FR-WZ-9: POST dismisses for good and is idempotent; the key is never logged."""
    row.enrollment_key_notice_dismissed = dismissed
    assert _call("post", log) == HIDDEN
    assert row.enrollment_key_notice_dismissed is True
    assert row.save.call_count == saves
    assert ("Enrollment key notice dismissed by boss." in log.text) is bool(saves)
    if saves:
        row.save.assert_called_once_with(update_fields=["enrollment_key_notice_dismissed"])
