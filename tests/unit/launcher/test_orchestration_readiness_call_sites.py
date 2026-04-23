# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Call-site integration tests for ``wait_for_manager_ready``.

Regression guard for GitHub issue #94: ``open_browser(...)`` must not
be invoked until after the readiness wait has returned (or short-
circuited because the active topology has no manager). Split out from
``test_orchestration_readiness.py`` to keep both files under the
300-line limit (CLAUDE.md).
"""

from __future__ import annotations

from launcher import orchestration


class TestCallSiteIntegration:
    """``open_browser`` must come after ``wait_for_manager_ready``."""

    def _make_args(self, mocker):
        return mocker.MagicMock(no_browser=False, print_url=False)

    def _attach_order_recorder(self, mocker):
        """Patch wait_for_manager_ready + open_browser with a shared
        parent so ``parent.mock_calls`` records call order across both.
        """
        parent = mocker.MagicMock()
        wait = mocker.MagicMock(return_value=True)
        opener = mocker.MagicMock()
        parent.attach_mock(wait, "wait_for_manager_ready")
        parent.attach_mock(opener, "open_browser")
        mocker.patch.object(orchestration, "wait_for_manager_ready", wait)
        mocker.patch.object(orchestration, "open_browser", opener)
        return parent, wait, opener

    def test_run_setup_mode_waits_before_opening_browser(
        self, mocker, tmp_path,
    ):
        parent, wait, opener = self._attach_order_recorder(mocker)

        manager_proc = mocker.MagicMock()
        # Returncode read after wait() -> int so the early-return path
        # in run_setup_mode short-circuits the IPC loop.
        manager_proc.wait.return_value = 0
        manager_proc.returncode = 0

        start_component = mocker.MagicMock(return_value=manager_proc)
        bootstrap = mocker.MagicMock(return_value=tmp_path)
        mocker.patch.object(
            orchestration, "find_available_port", return_value=8765,
        )
        mocker.patch.object(
            orchestration, "generate_setup_token", return_value="tok",
        )
        mocker.patch.object(orchestration, "print_setup_banner")
        mocker.patch.object(orchestration, "start_caddy_supervisor")

        rc = orchestration.run_setup_mode(
            tmp_path,
            self._make_args(mocker),
            tray=None,
            secret="s",
            start_component=start_component,
            bootstrap_first_run=bootstrap,
        )

        assert rc == 0
        # Order: readiness wait must precede the browser launch.
        names = [c[0] for c in parent.mock_calls]
        wait_idx = names.index("wait_for_manager_ready")
        open_idx = names.index("open_browser")
        assert wait_idx < open_idx, (
            f"open_browser called before wait_for_manager_ready: {names}"
        )
        wait.assert_called_once()
        opener.assert_called_once()

    def test_run_normal_mode_waits_before_opening_browser(
        self, mocker, tmp_path,
    ):
        parent, wait, opener = self._attach_order_recorder(mocker)

        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = 0  # exits the while loop
        worker_proc = mocker.MagicMock()
        worker_proc.poll.return_value = 0

        def _start(name, **_kw):
            return manager_proc if name == "manager" else worker_proc

        start = mocker.MagicMock(side_effect=_start)
        mocker.patch.object(
            orchestration, "_read_topology",
            return_value={"topology": "manager_worker"},
        )
        mocker.patch.object(orchestration, "remove_setup_section")
        mocker.patch.object(
            orchestration, "_consume_ipc", return_value=(None, None),
        )
        mocker.patch.object(orchestration.time, "sleep")
        mocker.patch.object(orchestration, "start_caddy_supervisor")

        rc = orchestration.run_normal_mode(
            tmp_path,
            self._make_args(mocker),
            tray=None,
            secret="s",
            start_component=start,
        )

        assert rc == 0
        names = [c[0] for c in parent.mock_calls]
        wait_idx = names.index("wait_for_manager_ready")
        open_idx = names.index("open_browser")
        assert wait_idx < open_idx
        # Sanity: only one wait, even though two procs were spawned.
        assert wait.call_count == 1
        opener.assert_called_once()

    def test_run_normal_mode_skips_wait_when_worker_only_topology(
        self, mocker, tmp_path,
    ):
        _parent, wait, opener = self._attach_order_recorder(mocker)

        worker_proc = mocker.MagicMock()
        worker_proc.poll.return_value = 0

        start = mocker.MagicMock(return_value=worker_proc)
        mocker.patch.object(
            orchestration, "_read_topology",
            return_value={"topology": "worker"},
        )
        mocker.patch.object(orchestration, "remove_setup_section")
        mocker.patch.object(
            orchestration, "_consume_ipc", return_value=(None, None),
        )
        mocker.patch.object(orchestration.time, "sleep")

        rc = orchestration.run_normal_mode(
            tmp_path,
            self._make_args(mocker),
            tray=None,
            secret="s",
            start_component=start,
        )

        assert rc == 0
        # No manager spawned -> nothing to wait on. But the browser
        # still opens to surface the dashboard URL/banner cleanly.
        wait.assert_not_called()
        opener.assert_called_once()
