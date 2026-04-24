# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Suppression tests for the launcher splash (FR-4 / TR-3).

The splash path must be skipped entirely when the launcher is invoked
with ``--no-browser`` or ``--print-url``.  In those cases the CI / Docker
fast path should neither build a QApplication nor import PySide6.

These tests assert the dispatch-level invariant by mocking the two
inner paths (``_main_with_splash`` and ``_main_headless``) and checking
which one ``main()`` calls.  The import-level guard is exercised
indirectly: ``_main_headless`` never touches the splash_runner module,
so PySide6 stays out of ``sys.modules`` on that path.
"""

from __future__ import annotations

import sys

import pytest

from launcher import run_launcher


@pytest.fixture
def fake_data_dir(tmp_path, mocker):
    """Isolate main() from the real user data dir + single-instance lock."""
    mocker.patch.object(run_launcher, "get_data_dir", return_value=tmp_path)
    mocker.patch.object(
        run_launcher, "acquire_single_instance_lock",
        return_value=object(),
    )
    mocker.patch.object(run_launcher, "release_lock")
    mocker.patch.object(
        run_launcher.supervision, "shutdown_supervisors",
    )
    mocker.patch.object(
        run_launcher.supervision, "get_shutdown_event",
    )
    # Skip the real logging configuration — it creates files.
    mocker.patch("launcher.logging_setup.configure")
    return tmp_path


class TestSplashSuppression:
    """``--no-browser`` / ``--print-url`` must skip _main_with_splash."""

    def test_no_browser_skips_splash_path(
        self, fake_data_dir, mocker, monkeypatch,
    ):
        headless = mocker.patch.object(
            run_launcher, "_main_headless", return_value=0,
        )
        with_splash = mocker.patch.object(
            run_launcher, "_main_with_splash", return_value=0,
        )
        monkeypatch.setattr(sys, "argv", ["run_launcher", "--no-browser"])

        rc = run_launcher.main()

        assert rc == 0
        headless.assert_called_once()
        with_splash.assert_not_called()

    def test_print_url_skips_splash_path(
        self, fake_data_dir, mocker, monkeypatch,
    ):
        headless = mocker.patch.object(
            run_launcher, "_main_headless", return_value=0,
        )
        with_splash = mocker.patch.object(
            run_launcher, "_main_with_splash", return_value=0,
        )
        monkeypatch.setattr(sys, "argv", ["run_launcher", "--print-url"])

        rc = run_launcher.main()

        assert rc == 0
        headless.assert_called_once()
        with_splash.assert_not_called()

    def test_normal_invocation_uses_splash_path(
        self, fake_data_dir, mocker, monkeypatch,
    ):
        headless = mocker.patch.object(
            run_launcher, "_main_headless", return_value=0,
        )
        with_splash = mocker.patch.object(
            run_launcher, "_main_with_splash", return_value=0,
        )
        monkeypatch.setattr(sys, "argv", ["run_launcher"])

        rc = run_launcher.main()

        assert rc == 0
        with_splash.assert_called_once()
        headless.assert_not_called()


class TestHeadlessPathDoesNotImportSplashRunner:
    """The headless branch must not pull in ``launcher.splash_runner``
    — that module imports PySide6 at module-scope, which would defeat
    the FR-4 CI/Docker fast path.
    """

    def test_main_headless_does_not_import_splash_runner(
        self, fake_data_dir, mocker, monkeypatch,
    ):
        # Stub out the inner orchestration so the test does not need
        # to spawn real subprocesses.
        mocker.patch.object(
            run_launcher, "_pre_orchestration_setup",
            return_value=(None, "secret"),
        )
        mocker.patch.object(
            run_launcher, "_run_orchestration", return_value=0,
        )
        mocker.patch.object(run_launcher, "_teardown_tray")

        # Snapshot sys.modules BEFORE the headless call instead of popping
        # PySide6 / splash_runner out. Popping orphaned Shiboken's C-level
        # type graph (it retains PyTypeObject* pointers to the stripped
        # modules' types), which then crashed the next PySide6 import in
        # a later test file with an access-violation on `Graph::identifyType`
        # (issue #117). The diff-based check below is behaviourally
        # equivalent: it still fails if main() NEWLY imports either module,
        # which is what FR-4 forbids.
        splash_runner_preloaded = "launcher.splash_runner" in sys.modules
        pyside_before = {
            k for k in sys.modules if k.startswith("PySide6")
        }

        monkeypatch.setattr(sys, "argv", ["run_launcher", "--no-browser"])
        rc = run_launcher.main()
        assert rc == 0

        if not splash_runner_preloaded:
            assert "launcher.splash_runner" not in sys.modules, (
                "Headless path must not import launcher.splash_runner "
                "(which transitively imports PySide6) — FR-4."
            )
        # Direct guard on the spec acceptance criterion: no NEW PySide6
        # submodule may appear after a headless boot. This catches any
        # future drive-by `import PySide6` slipped into the launcher's
        # startup path that would silently defeat the fast path (W1 from
        # code review).
        pyside_after = {
            k for k in sys.modules if k.startswith("PySide6")
        }
        newly_imported = pyside_after - pyside_before
        assert not newly_imported, (
            f"Headless path imported PySide6 modules: {sorted(newly_imported)}. "
            "FR-4 requires the --no-browser / --print-url paths to "
            "avoid Qt entirely."
        )
