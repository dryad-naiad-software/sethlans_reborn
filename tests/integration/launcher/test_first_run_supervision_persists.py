# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression test for issue #203 — wizard hand-off supervision.

Drives ``launcher.main_dispatch._run_orchestration`` end-to-end with
mocked components and asserts the supervision loop is entered after
the wizard returns rc=0. Pre-fix, ``_run_orchestration`` returned 0
immediately after ``run_wizard_mode`` returned 0 — Caddy was torn down
inside ``main()``'s finally block within ~1 s. Post-fix, the function
falls through to ``run_normal_mode``, which owns the ``while True``
supervision loop and keeps Caddy + the manager alive for the lifetime
of the launcher process.

What we assert:

1. ``_run_orchestration`` does NOT return until the supervision loop
   sees a quit signal.
2. ``start_caddy_supervisor`` was called (Caddy was spawned by
   ``run_normal_mode``, not torn down by ``hand_off_to_runtime``).
3. ``_start_component`` was called for ``"manager"`` (manager was
   spawned by ``run_normal_mode``).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from launcher import main_dispatch, orchestration, supervision


@pytest.fixture(autouse=True)
def _clear_quit_event():
    supervision.get_quit_requested_event().clear()
    yield
    supervision.get_quit_requested_event().clear()


def _write_topology(data_dir, topo="manager_worker"):
    (data_dir / "topology.json").write_text(
        f'{{"topology": "{topo}"}}', encoding="utf-8",
    )


def _write_manager_ini(manager_data):
    manager_data.mkdir(parents=True, exist_ok=True)
    (manager_data / "manager.ini").write_text(
        "[server]\nport = 8080\n", encoding="utf-8",
    )


class TestFirstRunSupervisionPersists:
    """#203 regression: wizard fall-through enters the supervision loop."""

    def test_orchestration_blocks_until_quit_event_set(
        self, tmp_path, mocker,
    ):
        # 1. Wizard succeeds: write sentinel + return 0. This emulates
        # the apply pipeline's contract — .setup_complete is the
        # success signal for the defensive guard.
        def _fake_wizard(data_dir, *_a, **_kw):
            (data_dir / ".setup_complete").touch()
            return 0

        run_wizard = mocker.patch.object(
            main_dispatch.wizard_orchestration, "run_wizard_mode",
            side_effect=_fake_wizard,
        )

        # 2. Topology + manager.ini so run_normal_mode resolves a port.
        _write_topology(tmp_path)

        def _bootstrap(d):
            _write_manager_ini(d / "manager")
            return d / "manager"
        bootstrap = MagicMock(side_effect=_bootstrap)

        # 3. Stub the Caddy + manager spawn so run_normal_mode owns
        # nothing real. start_caddy_supervisor records the call;
        # _start_component returns a long-lived fake process so the
        # cold-boot probe + supervision loop have something to watch.
        start_caddy = mocker.patch.object(
            orchestration, "start_caddy_supervisor",
        )
        mocker.patch.object(orchestration, "remove_setup_section")
        # The cold-boot health gate must succeed so the supervision
        # loop is reached. Mock it to return None (None = no early
        # exit, fall through to the while True loop).
        mocker.patch.object(
            orchestration, "_await_cold_boot", return_value=None,
        )
        # Don't pop a real browser window in CI.
        mocker.patch.object(orchestration, "open_browser")

        # Fake process: stays alive for the whole test.
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.pid = 99999
        start_component = MagicMock(return_value=fake_proc)

        # 4. Drive _run_orchestration in a thread. The main test
        # thread sets the quit event after a short delay so the
        # supervision loop can break out (mirrors a tray quit).
        result_holder: dict = {}

        def _target():
            result_holder["rc"] = main_dispatch._run_orchestration(
                tmp_path, MagicMock(no_browser=True, print_url=True),
                tray=None, secret="ipc-secret",
                bootstrap_first_run=bootstrap,
                start_component=start_component,
            )

        t = threading.Thread(target=_target, daemon=True)
        t.start()

        # Give the orchestration enough time to reach the supervision
        # loop. 0.5 s is comfortably above the per-iteration sleep
        # (RESTART_POLL_INTERVAL = 2 s) for the inner wait, but the
        # quit event short-circuits that wait via wait_or_quit.
        time.sleep(0.5)

        # Pre-fix: the thread already exited at this point (rc=0 from
        # wizard mode). Post-fix: the thread is parked in the while
        # True supervision loop, waiting on the quit event.
        assert t.is_alive(), (
            "regression #203: _run_orchestration returned without "
            "entering the supervision loop — Caddy would be torn down "
            "immediately after wizard handoff"
        )

        # 5. Signal quit. The supervision loop should observe it via
        # wait_or_quit and break out of the while True.
        supervision.get_quit_requested_event().set()
        t.join(timeout=5.0)
        assert not t.is_alive(), (
            "_run_orchestration did not return within 5s after the "
            "quit event was set — supervision loop did not break out"
        )

        assert result_holder.get("rc") == 0
        # Wizard ran exactly once and Caddy + manager were spawned by
        # run_normal_mode (i.e. fall-through worked).
        run_wizard.assert_called_once()
        start_caddy.assert_called_once_with(tmp_path / "manager")
        # _start_component called at least once for "manager".
        manager_calls = [
            c for c in start_component.call_args_list
            if c.args and c.args[0] == "manager"
        ]
        assert len(manager_calls) >= 1, (
            "run_normal_mode did not spawn the manager — fall-through "
            "to start_component(\"manager\") did not happen"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
