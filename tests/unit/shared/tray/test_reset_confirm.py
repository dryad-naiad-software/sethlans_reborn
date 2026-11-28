# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/reset_confirm.py``: Reset setup behind one confirm dialog (FR-RS-1, security L5)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for the tray")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from shared.tray import ipc, reset_confirm  # noqa: E402
from tests.unit.shared.tray.conftest import write_launcher_state  # noqa: E402

BUTTONS = QMessageBox.StandardButton


@pytest.fixture(autouse=True)
def _no_open_dialog():
    yield
    box = reset_confirm._open_box
    if box is not None:
        box.close()
    reset_confirm._open_box = None


@pytest.mark.parametrize(("state", "sent"), [
    ("RUNNING", True), ("FAILED", True), ("SETUP", False), ("STARTING", False), ("STOPPING", False),
])
def test_reset_is_sent_only_where_the_launcher_acts_on_it(settings_dir, state, sent):
    """Security L5: the state is read again on Reset; outside RUNNING/FAILED nothing is
    written and the person is told."""
    write_launcher_state(settings_dir, state)
    notices = []
    assert reset_confirm.send_reset(settings_dir, notices.append) is sent
    assert (settings_dir / ipc.MARKER_RESET).exists() is sent
    assert len(notices) == (0 if sent else 1)


def test_a_marker_that_cannot_be_written_is_reported(settings_dir, mocker):
    """FR-RS-1: a failed write is told to the person, never raised from the click."""
    write_launcher_state(settings_dir, "RUNNING")
    mocker.patch.object(reset_confirm.ipc, "request_reset", side_effect=OSError("disk full"))
    notices = []
    assert reset_confirm.send_reset(settings_dir, notices.append) is False
    assert [n.message for n in notices] == ["Reset could not be sent. See the logs."]


@pytest.mark.parametrize(("button", "sent"), [(BUTTONS.Reset, True), (BUTTONS.Cancel, False)])
def test_dialog_sends_only_on_reset(qapp, settings_dir, mocker, button, sent):
    """FR-RS-1: Reset asks for confirmation; Cancel sends nothing."""
    send = mocker.patch.object(reset_confirm, "send_reset")
    reset_confirm.ask_reset(settings_dir)
    box = reset_confirm._open_box
    assert box.defaultButton() is box.button(BUTTONS.Cancel)
    box.button(button).click()
    assert send.called is sent
    assert reset_confirm._open_box is None


def test_one_dialog_at_a_time(qapp, settings_dir):
    """FR-RS-1: clicking Reset again while the dialog is open shows the same dialog."""
    reset_confirm.ask_reset(settings_dir)
    first = reset_confirm._open_box
    reset_confirm.ask_reset(settings_dir)
    assert reset_confirm._open_box is first
