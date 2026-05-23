# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``run_launcher._run_orchestration`` routing — wizard vs. normal mode.

Split from ``test_wizard_orchestration.py`` so that file stays under
the 300-line ceiling (CLAUDE.md). Verifies that the launcher routes
first-run to ``run_wizard_mode`` and post-setup to ``run_normal_mode``.
"""

from __future__ import annotations

import argparse


class TestRunLauncherWizardWiring:
    """Verify run_launcher._run_orchestration routes first-run to wizard."""

    def test_first_run_calls_wizard_mode(self, tmp_path, mocker):
        from launcher import run_launcher
        # Issue #203: wizard mode now FALLS THROUGH to run_normal_mode
        # after rc=0, so this test asserts both are called. The wizard
        # must "write" .setup_complete to pass the defensive guard.
        def _fake_wizard(data_dir, *_a, **_kw):
            (data_dir / ".setup_complete").touch()
            return 0
        run_wizard = mocker.patch(
            "launcher.main_dispatch.wizard_orchestration.run_wizard_mode",
            side_effect=_fake_wizard,
        )
        run_normal = mocker.patch(
            "launcher.main_dispatch.orchestration.run_normal_mode",
            return_value=0,
        )

        rc = run_launcher._run_orchestration(
            tmp_path, argparse.Namespace(no_browser=True, print_url=True),
            tray=None, secret="dummy-secret",
        )
        assert rc == 0
        run_wizard.assert_called_once()
        run_normal.assert_called_once()

    def test_post_setup_calls_normal_mode(self, tmp_path, mocker):
        from launcher import run_launcher
        # Sentinel present → post-setup path.
        (tmp_path / ".setup_complete").write_text("{}")
        run_wizard = mocker.patch(
            "launcher.main_dispatch.wizard_orchestration.run_wizard_mode",
            return_value=0,
        )
        run_normal = mocker.patch(
            "launcher.main_dispatch.orchestration.run_normal_mode",
            return_value=0,
        )

        rc = run_launcher._run_orchestration(
            tmp_path, argparse.Namespace(no_browser=True, print_url=True),
            tray=None, secret="dummy-secret",
        )
        assert rc == 0
        run_normal.assert_called_once()
        run_wizard.assert_not_called()
