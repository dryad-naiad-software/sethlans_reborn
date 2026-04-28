# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #163 follow-up — normal-mode steady-state tray quit.

Concurrency-reviewer regression: after the cold-boot probes complete,
the IPC poll daemon thread (in :mod:`launcher.supervision`) consumes
``.quit_requested`` markers and sets the process-wide quit event. The
normal-mode main loop must therefore observe that event each
iteration; otherwise a tray quit click is silently lost (the daemon
ate the marker, ``_consume_ipc`` reads nothing on disk, and the
cascade never fires).

Covers:

* Event-set at iteration top fires ``cascade.cascade_quit`` and
  returns 0.
* ``wait_or_quit`` is called between iterations so a tray quit during
  the sleep wakes the loop within ~one poll interval — well under
  ``RESTART_POLL_INTERVAL``.
* Second-quit click during cascade still triggers the FR-21 fast-path
  SIGKILL via the event-aware path: the daemon does not consume the
  marker while the event is already set, so ``_make_second_quit_check``
  finds it on disk via ``_consume_ipc``.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from launcher import orchestration, supervision

from ._tray_quit_helpers import FakeProc, write_quit_marker


# ----------------------------------------------------------------------
# Event-set at iteration top fires the cascade
# ----------------------------------------------------------------------

class TestNormalModeMainLoopCascadesOnQuitEvent:

    def test_event_set_runs_cascade_and_returns_zero(self, mocker, tmp_path):
        cascade_spy = mocker.patch.object(
            orchestration.cascade, "cascade_quit",
        )
        manager_proc = FakeProc()
        worker_proc = FakeProc()
        state = {
            "manager": manager_proc,
            "worker": worker_proc,
            "cascade": False,
        }
        supervision.get_quit_requested_event().set()

        rc = orchestration._normal_mode_main_loop_iteration(
            state, tmp_path, "s" * 48, tray=None,
            start_component=lambda *_a, **_k: MagicMock(),
        )

        assert rc == 0
        cascade_spy.assert_called_once()
        # FR-21 second-quit-check must be wired so a 2nd click can
        # escalate the cascade to immediate SIGKILL.
        kwargs = cascade_spy.call_args.kwargs
        assert kwargs["second_quit_check"] is not None

    def test_event_set_with_no_data_dir_secret_still_cascades(self, mocker):
        # ``_handle_quit`` accepts None data_dir/secret; the event-set
        # path always provides them, but verify the helper signature
        # does not require them (defensive — keeps the function easy
        # to reuse from tests).
        cascade_spy = mocker.patch.object(
            orchestration.cascade, "cascade_quit",
        )
        state = {
            "manager": FakeProc(), "worker": FakeProc(), "cascade": False,
        }
        rc = orchestration._handle_quit("all", state, tray=None)
        assert rc == 0
        cascade_spy.assert_called_once()


# ----------------------------------------------------------------------
# wait_or_quit drives sub-poll-interval responsiveness
# ----------------------------------------------------------------------

class TestNormalModeMainLoopWakesOnQuitDuringSleep:
    """The main-loop ``wait_or_quit`` call must return early on a
    quit event so the cascade fires within ~IPC_POLL_INTERVAL_SECONDS
    rather than waiting out the full ``RESTART_POLL_INTERVAL`` (2s).
    """

    def test_loop_wakes_within_250ms_of_event_being_set(
        self, mocker, tmp_path,
    ):
        # Stub the per-iteration helper so the loop body is essentially
        # ``while True: wait_or_quit(...)``. Returning None means
        # "keep looping"; the loop should wake when the event is set
        # mid-sleep and the next iteration observes the event and
        # returns 0 via the cascade path.
        call_count = {"n": 0}

        def _iter(state, *_a, **_kw):
            call_count["n"] += 1
            # First iteration: keep looping (no event yet).
            # Subsequent iterations: real helper would observe the
            # event; we short-circuit to 0 to terminate.
            if supervision.get_quit_requested_event().is_set():
                return 0
            return None

        mocker.patch.object(
            orchestration, "_normal_mode_main_loop_iteration",
            side_effect=_iter,
        )
        # Skip everything before the loop.
        mocker.patch.object(orchestration, "_read_topology",
                            return_value={"topology": "worker"})
        mocker.patch.object(orchestration, "remove_setup_section")
        mocker.patch.object(orchestration, "_await_cold_boot",
                            return_value=None)
        mocker.patch.object(orchestration, "open_browser")

        # Use a long RESTART_POLL_INTERVAL so a slow-path ``time.sleep``
        # fallback would clearly fail this test (>1s).
        mocker.patch.object(orchestration, "RESTART_POLL_INTERVAL", 5.0)

        import argparse
        args = argparse.Namespace(no_browser=True, print_url=True)

        result = {}

        def _runner():
            result["rc"] = orchestration.run_normal_mode(
                tmp_path, args, tray=None, secret="s" * 48,
                start_component=lambda *_a, **_k: FakeProc(),
            )

        loop_thread = threading.Thread(target=_runner, daemon=True)
        loop_thread.start()

        # Let the loop reach the first ``wait_or_quit`` call.
        time.sleep(0.05)
        t0 = time.monotonic()
        supervision.get_quit_requested_event().set()
        loop_thread.join(timeout=2.0)
        elapsed = time.monotonic() - t0

        assert not loop_thread.is_alive(), (
            "Loop did not wake on quit event within 2s"
        )
        assert result.get("rc") == 0
        # Generous bound: 250 ms is plenty of slack vs. the 5 s
        # RESTART_POLL_INTERVAL we patched in. CI machines under load
        # are still well under this.
        assert elapsed < 0.25, (
            f"Loop took {elapsed:.3f}s to wake; expected <250 ms"
        )
        # Loop iterated at least twice: once before the event, once
        # after — proving ``wait_or_quit`` returned early.
        assert call_count["n"] >= 2


# ----------------------------------------------------------------------
# Second click still triggers FR-21 SIGKILL fast path
# ----------------------------------------------------------------------

class TestSecondQuitClickTriggersSigkillViaEventPath:
    """FR-21: a second tray quit click during the cascade must SIGKILL
    survivors immediately rather than letting the grace timers run out.

    With the daemon-thread quit consumption added in #163, the daemon
    skips marker consumption while the quit event is already set.
    A second-click marker therefore stays on disk and is found by
    ``_make_second_quit_check`` via ``_consume_ipc``.
    """

    def test_second_quit_check_returns_true_for_second_marker(
        self, tmp_path,
    ):
        # Simulate the post-first-click state: event set, daemon would
        # skip ``.quit_requested`` consumption (verified by the
        # supervision-level test below). The on-disk marker therefore
        # stays put for the cascade's poll callback to find.
        secret = "z" * 48
        tray_pid = 42
        # Build a fake tray ``Popen`` with the expected pid.
        tray = FakeProc(pid=tray_pid)
        write_quit_marker(tmp_path, secret, tray_pid)

        check = orchestration._make_second_quit_check(
            tmp_path, secret, tray,
        )
        assert check() is True
        # And the marker is consumed (deleted) by the read.
        assert not (tmp_path / ".quit_requested").exists()

    def test_daemon_skips_consumption_while_event_already_set(
        self, mocker, tmp_path,
    ):
        # Direct unit test of the daemon's "skip if event set"
        # behavior — load-bearing for the second-click flow above.
        manager_data = tmp_path / "manager"
        manager_data.mkdir()
        secret = "y" * 48
        tray_pid = 7777
        write_quit_marker(tmp_path, secret, tray_pid)
        # Pre-set the event (simulates the first click already
        # consumed by the daemon on a prior tick).
        supervision.get_quit_requested_event().set()

        # Spy on consume_pending_ipc to confirm the daemon helper
        # bails out before reaching it.
        from launcher import tray_ipc
        spy = mocker.spy(tray_ipc, "consume_pending_ipc")

        supervision._maybe_observe_tray_quit(
            manager_data, secret=secret,
            tray_pid_provider=lambda: tray_pid,
        )

        spy.assert_not_called()
        # Marker must still be on disk for the main loop / cascade
        # second_quit_check to read.
        assert (tmp_path / ".quit_requested").exists()
