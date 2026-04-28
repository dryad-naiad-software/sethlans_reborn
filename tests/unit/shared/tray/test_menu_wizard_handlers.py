# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Click-handler + refresh-gating tests for ``shared/tray/menu_wizard.py``.

Covers spec acceptance criteria:

* AC-OpenWizardUrl — bare URL, NO ``?token=`` / ``#token``.
* AC-CopyToken — reads from ``<data_dir>/wizard/.setup_token`` only.
* FR-9 — View Setup Wizard Logs prefers ``wizard.log`` and falls back
  to ``launcher.log``.
* FR-10 — Open Setup Wizard / Copy Setup Token greyed out when
  ``read_wizard_state`` returns None.
* AC-NoQuitRegression — wizard's Quit Sethlans uses ``target="all"``.
* NFR-7 — token value never appears in log records.
"""

from __future__ import annotations

import logging
import threading

import pytest

pytest.importorskip(
    "PySide6", reason="PySide6 required for menu_wizard",
)
pytest.importorskip(
    "pytestqt", reason="pytest-qt required for qapp fixture",
)

from shared.tray import menu_wizard as qmw  # noqa: E402
from shared.tray.menu_wizard import WizardSection  # noqa: E402
from shared.tray.notifications import NotificationEvent  # noqa: E402
from shared.tray.wizard_state import WizardState  # noqa: E402


def _write_wizard_files(tmp_path, port=8443, token="tok-abc"):
    """Helper: create ``<tmp_path>/wizard/{port,.setup_token}``."""
    wd = tmp_path / "wizard"
    wd.mkdir(exist_ok=True)
    (wd / "port").write_text(str(port), encoding="utf-8")
    (wd / ".setup_token").write_text(token, encoding="utf-8")
    return wd


@pytest.fixture
def section(qapp, tmp_path):
    """Bare ``WizardSection`` rooted at ``tmp_path`` (no wizard files)."""
    return WizardSection(
        data_dir=tmp_path,
        quit_requested_flag=threading.Event(),
        get_version=lambda: "9.9.9",
    )


@pytest.fixture
def section_with_files(qapp, tmp_path):
    """``WizardSection`` with valid wizard/port + wizard/.setup_token."""
    _write_wizard_files(tmp_path)
    return WizardSection(
        data_dir=tmp_path,
        quit_requested_flag=threading.Event(),
        get_version=lambda: "9.9.9",
    )


# ------------------------------------------------------------------ #
# on_open_wizard — bare URL, no token in query / fragment
# ------------------------------------------------------------------ #

class TestOpenWizardHandler:

    def test_uses_wizard_state_port(self, section_with_files, mocker):
        """AC-OpenWizardUrl: URL is ``https://localhost:<port>/``."""
        spy = mocker.patch.object(qmw.webbrowser, "open")
        section_with_files.on_open_wizard()
        spy.assert_called_once_with("https://localhost:8443/")

    def test_url_has_no_token_query_string(
        self, section_with_files, mocker,
    ):
        """FR-7: wizard backend rejects ``?token=`` and Chrome strips
        the query string anyway. URL must be bare."""
        spy = mocker.patch.object(qmw.webbrowser, "open")
        section_with_files.on_open_wizard()
        url = spy.call_args[0][0]
        assert "?" not in url, f"URL must not contain query string: {url}"
        assert "token" not in url.lower(), (
            f"URL must not mention token: {url}"
        )

    def test_url_has_no_token_fragment(
        self, section_with_files, mocker,
    ):
        spy = mocker.patch.object(qmw.webbrowser, "open")
        section_with_files.on_open_wizard()
        url = spy.call_args[0][0]
        assert "#" not in url, f"URL must not contain fragment: {url}"

    def test_url_uses_https_scheme(self, section_with_files, mocker):
        spy = mocker.patch.object(qmw.webbrowser, "open")
        section_with_files.on_open_wizard()
        url = spy.call_args[0][0]
        assert url.startswith("https://"), url

    def test_uses_localhost_not_arbitrary_host(
        self, section_with_files, mocker,
    ):
        spy = mocker.patch.object(qmw.webbrowser, "open")
        section_with_files.on_open_wizard()
        url = spy.call_args[0][0]
        # The wizard runs locally; URL must be localhost.
        assert "localhost" in url, url

    def test_noop_when_state_unavailable(self, section, mocker, caplog):
        """No port file written -> no webbrowser.open call, warning
        logged. FR-7."""
        spy = mocker.patch.object(qmw.webbrowser, "open")
        with caplog.at_level(logging.WARNING, logger=qmw.logger.name):
            section.on_open_wizard()
        spy.assert_not_called()
        assert any(
            "wizard state unavailable" in rec.getMessage().lower()
            for rec in caplog.records
        )

    def test_uses_dynamic_port_value(self, qapp, tmp_path, mocker):
        """The port is sourced from wizard/port, not a hardcoded
        constant."""
        _write_wizard_files(tmp_path, port=12345)
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
        )
        spy = mocker.patch.object(qmw.webbrowser, "open")
        sec.on_open_wizard()
        spy.assert_called_once_with("https://localhost:12345/")


# ------------------------------------------------------------------ #
# on_copy_token — reads wizard subdir, never manager.ini
# ------------------------------------------------------------------ #

class TestCopyTokenHandler:

    def test_reads_from_wizard_subdir_not_manager_ini(
        self, section_with_files, mocker,
    ):
        """AC-CopyToken: reads from ``<data_dir>/wizard/.setup_token``,
        NOT ``<manager_data_dir>/manager.ini``."""
        copy_spy = mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=True,
        )
        # Patch read_wizard_state to verify it's the data source.
        rws_spy = mocker.spy(qmw, "read_wizard_state")
        section_with_files.on_copy_token()
        rws_spy.assert_called_once_with(section_with_files.data_dir)
        # The clipboard helper receives the token from the wizard files.
        copy_spy.assert_called_once_with("tok-abc")

    def test_does_not_read_manager_ini(self, section_with_files, mocker):
        """Defensive: even with a manager.ini present, copy_token MUST
        NOT touch it; the wizard files are authoritative."""
        # Plant a manager.ini that, if mistakenly read, would give a
        # very different token.
        (section_with_files.data_dir / "manager.ini").write_text(
            "[setup]\ntoken = wrong-token-from-ini\n",
            encoding="utf-8",
        )
        copy_spy = mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=True,
        )
        section_with_files.on_copy_token()
        copy_spy.assert_called_once_with("tok-abc")

    def test_noop_when_state_unavailable(self, section, mocker):
        """Missing wizard/port + wizard/.setup_token -> no clipboard
        call (action would have been greyed out, but the slot stays
        defensive anyway)."""
        copy_spy = mocker.patch.object(qmw, "copy_token_to_clipboard")
        section.on_copy_token()
        copy_spy.assert_not_called()

    def test_dispatches_notification_on_success(self, qapp, tmp_path, mocker):
        _write_wizard_files(tmp_path)
        notify = mocker.MagicMock()
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
            notify=notify,
        )
        mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=True,
        )
        sec.on_copy_token()
        notify.assert_called_once()
        evt = notify.call_args[0][0]
        assert isinstance(evt, NotificationEvent)
        assert evt.title == "Token copied"
        assert evt.message == "Setup token copied to clipboard."

    def test_no_notification_on_failure(self, qapp, tmp_path, mocker):
        _write_wizard_files(tmp_path)
        notify = mocker.MagicMock()
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
            notify=notify,
        )
        mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=False,
        )
        sec.on_copy_token()
        notify.assert_not_called()

    def test_no_notification_when_notify_is_none(
        self, section_with_files, mocker,
    ):
        """notify=None must not raise; just no notification fires."""
        mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=True,
        )
        # Must not raise.
        section_with_files.on_copy_token()

    def test_token_value_never_in_notification(self, qapp, tmp_path, mocker):
        """Defense-in-depth: notification carries no token data."""
        secret = "ULTRA-SECRET-TOKEN-do-not-leak"
        _write_wizard_files(tmp_path, token=secret)
        captured = []
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
            notify=lambda evt: captured.append(evt),
        )
        mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=True,
        )
        sec.on_copy_token()
        assert len(captured) == 1
        evt = captured[0]
        assert secret not in evt.title
        assert secret not in evt.message

    def test_token_value_never_logged(
        self, qapp, tmp_path, mocker, caplog,
    ):
        """NFR-7: token value never logged on any path (success or
        failure)."""
        secret = "ULTRA-SECRET-TOKEN-do-not-leak"
        _write_wizard_files(tmp_path, token=secret)
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
        )
        mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=True,
        )
        with caplog.at_level(logging.DEBUG, logger=qmw.logger.name):
            sec.on_copy_token()
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert secret not in combined

    def test_notify_exception_is_swallowed(
        self, qapp, tmp_path, mocker,
    ):
        _write_wizard_files(tmp_path)
        notify = mocker.MagicMock(side_effect=RuntimeError("boom"))
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
            notify=notify,
        )
        mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=True,
        )
        # Must not raise — notify failures are swallowed.
        sec.on_copy_token()


# ------------------------------------------------------------------ #
# on_view_logs — wizard.log preferred, fall back to launcher.log
# ------------------------------------------------------------------ #

class TestViewLogsHandler:

    def test_prefers_wizard_log(self, section, mocker):
        """FR-9: ``wizard.log`` exists -> open it."""
        logs = section.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "wizard.log").write_text("wizard log", encoding="utf-8")
        (logs / "launcher.log").write_text("launcher", encoding="utf-8")
        spy = mocker.patch.object(qmw, "open_logs")
        section.on_view_logs()
        spy.assert_called_once_with(section.data_dir, "wizard.log")

    def test_falls_back_to_launcher_log(self, section, mocker):
        """FR-9: no ``wizard.log`` -> open ``launcher.log``."""
        logs = section.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "launcher.log").write_text("launcher", encoding="utf-8")
        # Note: NO wizard.log exists.
        spy = mocker.patch.object(qmw, "open_logs")
        section.on_view_logs()
        spy.assert_called_once_with(section.data_dir, "launcher.log")

    def test_calls_open_logs_helper_with_data_dir(self, section, mocker):
        """Slot delegates to ``menu_manager_helpers.open_logs`` (the
        cross-platform launcher switch lives there, not inlined in
        the handler)."""
        logs = section.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "wizard.log").write_text("x", encoding="utf-8")
        spy = mocker.patch.object(qmw, "open_logs")
        section.on_view_logs()
        args, _ = spy.call_args
        assert args[0] == section.data_dir


# ------------------------------------------------------------------ #
# on_quit_sethlans — target="all", flag set
# ------------------------------------------------------------------ #

class TestQuitHandler:
    """AC-NoQuitRegression: wizard menu's Quit must use ``target="all"``,
    NOT ``target="manager"`` (no manager exists in wizard mode)."""

    def test_uses_target_all(self, section, mocker):
        spy = mocker.patch.object(qmw.ipc, "request_quit")
        section.on_quit_sethlans()
        spy.assert_called_once_with(section.data_dir, target="all")

    def test_does_not_use_target_manager(self, section, mocker):
        spy = mocker.patch.object(qmw.ipc, "request_quit")
        section.on_quit_sethlans()
        # Asserted positively above, but pin defensively too.
        _, kwargs = spy.call_args
        assert kwargs["target"] != "manager"

    def test_sets_quit_flag(self, section, mocker):
        mocker.patch.object(qmw.ipc, "request_quit")
        assert not section.quit_flag.is_set()
        section.on_quit_sethlans()
        assert section.quit_flag.is_set()

    def test_request_quit_failure_does_not_propagate(self, section, mocker):
        mocker.patch.object(
            qmw.ipc, "request_quit",
            side_effect=RuntimeError("ipc broken"),
        )
        # Must not raise — flag was set first, exception swallowed.
        section.on_quit_sethlans()
        assert section.quit_flag.is_set()


# ------------------------------------------------------------------ #
# on_about — show_about_dialog
# ------------------------------------------------------------------ #

class TestAboutHandler:

    def test_invokes_show_about_dialog(self, section, mocker):
        spy = mocker.patch.object(qmw, "show_about_dialog")
        section.build_qmenu()
        section.on_about()
        spy.assert_called_once()

    def test_passes_menu_as_parent(self, section, mocker):
        spy = mocker.patch.object(qmw, "show_about_dialog")
        section.build_qmenu()
        section.on_about()
        args, _ = spy.call_args
        assert args[0] is section._menu


# ------------------------------------------------------------------ #
# refresh — gating (FR-10): grey out wizard actions when state unready
# ------------------------------------------------------------------ #

class TestRefreshGating:

    def test_actions_disabled_when_state_unavailable(self, section):
        """Section built with no wizard files -> Open Setup Wizard +
        Copy Setup Token are disabled."""
        section.build_qmenu()
        # No wizard/port written.
        assert section._act_open_wizard.isEnabled() is False
        assert section._act_copy_token.isEnabled() is False

    def test_actions_enabled_when_state_available(self, section_with_files):
        """Section built with wizard/port + wizard/.setup_token ->
        actions are enabled."""
        section_with_files.build_qmenu()
        assert section_with_files._act_open_wizard.isEnabled() is True
        assert section_with_files._act_copy_token.isEnabled() is True

    def test_refresh_re_evaluates_state(self, section, mocker):
        """Wire-on-tick scenario: section starts with no files; after
        files appear and refresh() runs, actions flip to enabled."""
        section.build_qmenu()
        assert section._act_open_wizard.isEnabled() is False

        # Simulate launcher writing the wizard files mid-flight.
        _write_wizard_files(section.data_dir)
        section.refresh()
        assert section._act_open_wizard.isEnabled() is True
        assert section._act_copy_token.isEnabled() is True

    def test_quit_disabled_when_quit_marker_present(
        self, section_with_files, mocker,
    ):
        """FR-10: Quit Sethlans is gated by ``ipc.marker_exists``."""
        mocker.patch.object(
            qmw.ipc, "marker_exists", return_value=True,
        )
        section_with_files.build_qmenu()
        assert section_with_files._act_quit.isEnabled() is False

    def test_quit_enabled_without_marker(self, section_with_files, mocker):
        mocker.patch.object(
            qmw.ipc, "marker_exists", return_value=False,
        )
        section_with_files.build_qmenu()
        assert section_with_files._act_quit.isEnabled() is True

    def test_wizard_actions_enabled_predicate_uses_read_wizard_state(
        self, section, mocker,
    ):
        """``wizard_actions_enabled()`` returns True iff
        ``read_wizard_state`` returns non-None — independent of what's
        on disk."""
        mocker.patch.object(
            qmw, "read_wizard_state",
            return_value=WizardState(port=8443, token="t"),
        )
        assert section.wizard_actions_enabled() is True

        mocker.patch.object(qmw, "read_wizard_state", return_value=None)
        assert section.wizard_actions_enabled() is False


# ------------------------------------------------------------------ #
# Triggered wiring — QAction.trigger() reaches handlers
# ------------------------------------------------------------------ #

class TestTriggeredWiring:

    def test_open_wizard_triggered_invokes_handler(
        self, section_with_files, mocker,
    ):
        spy = mocker.patch.object(qmw.webbrowser, "open")
        section_with_files.build_qmenu()
        section_with_files._act_open_wizard.trigger()
        spy.assert_called_once_with("https://localhost:8443/")

    def test_copy_token_triggered_invokes_handler(
        self, section_with_files, mocker,
    ):
        spy = mocker.patch.object(
            qmw, "copy_token_to_clipboard", return_value=True,
        )
        section_with_files.build_qmenu()
        section_with_files._act_copy_token.trigger()
        spy.assert_called_once_with("tok-abc")

    def test_quit_triggered_invokes_handler(self, section, mocker):
        spy = mocker.patch.object(qmw.ipc, "request_quit")
        section.build_qmenu()
        section._act_quit.trigger()
        spy.assert_called_once_with(section.data_dir, target="all")

    def test_about_triggered_invokes_handler(self, section, mocker):
        spy = mocker.patch.object(qmw, "show_about_dialog")
        section.build_qmenu()
        section._act_about.trigger()
        spy.assert_called_once()


# ------------------------------------------------------------------ #
# AC-NoSplashRegression: wizard menu MUST NOT touch splash APIs
# ------------------------------------------------------------------ #

class TestNoSplashAPIs:
    """Spec AC-NoSplashRegression: phase-swap logic must not call
    ``cold_boot_ready`` / ``startup_failed`` from the tray. The wizard
    menu module should not even import the splash subsystem."""

    def test_menu_wizard_does_not_import_splash(self):
        import shared.tray.menu_wizard as mod
        src_text = open(mod.__file__, encoding="utf-8").read()
        assert "cold_boot_ready" not in src_text
        assert "startup_failed" not in src_text
        # Splash module names — shouldn't appear anywhere.
        assert "splash" not in src_text.lower()
