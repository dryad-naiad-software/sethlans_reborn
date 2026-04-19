# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/notifications.py`` (FR-25 / FR-25a)."""

from __future__ import annotations

import logging
import sys

import pytest

from shared.tray import notifications
from shared.tray.notifications import NotificationEvent, dispatch


@pytest.fixture
def _fake_plyer(mocker):
    """Install a fake plyer.notification that captures calls."""
    fake = mocker.MagicMock()
    mocker.patch.dict(sys.modules, {"plyer": mocker.MagicMock(
        notification=fake,
    )})
    return fake


class TestDispatchHappyPath:

    def test_calls_plyer_notification_notify(self, _fake_plyer):
        evt = NotificationEvent(title="T", message="M")
        dispatch(evt)
        _fake_plyer.notify.assert_called_once()
        kwargs = _fake_plyer.notify.call_args.kwargs
        assert kwargs["title"] == "T"
        assert kwargs["message"] == "M"
        assert kwargs["app_name"] == notifications.APP_NAME


class TestDispatchSwallowsExceptions:

    def test_generic_exception_is_swallowed_and_logged(
        self, _fake_plyer, caplog,
    ):
        _fake_plyer.notify.side_effect = RuntimeError("kaboom")
        with caplog.at_level(logging.WARNING, logger=notifications.logger.name):
            try:
                dispatch(NotificationEvent("T", "M"))
            except Exception as exc:  # pragma: no cover
                pytest.fail(f"dispatch raised: {exc!r}")
        assert any(
            "Notification dispatch failed" in rec.message
            for rec in caplog.records
        )

    def test_oserror_is_swallowed(self, _fake_plyer):
        _fake_plyer.notify.side_effect = OSError("win32 dll fail")
        # Must not raise.
        dispatch(NotificationEvent("T", "M"))

    def test_plyer_import_failure_does_not_raise(self, mocker, caplog):
        # Force import to raise inside dispatch.
        mocker.patch.dict(sys.modules, {"plyer": None})
        dispatch(NotificationEvent("T", "M"))


class TestNotificationEventImmutable:

    def test_event_is_frozen_dataclass(self):
        evt = NotificationEvent("T", "M")
        with pytest.raises(Exception):
            evt.title = "other"  # type: ignore[misc]
