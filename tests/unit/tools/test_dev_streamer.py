# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #176 layer 2 — Ctrl+C propagation tests for the shared
dev-script subprocess streamer in :mod:`tools._dev_streamer`.

The dev_*.py harness scripts spawn their target component as a
subprocess. Before the fix, Ctrl+C in the parent terminal called
``proc.terminate()`` on the child after a 5 s grace — but never
forwarded the SIGINT first, so the child's own polite-shutdown
machinery (e.g. wizard's signal handler unblocking ``server.run()``)
never ran.

These tests exercise the platform-specific spawn kwargs
(``start_new_session`` on POSIX, ``CREATE_NEW_PROCESS_GROUP`` on
Windows) and the SIGINT -> SIGTERM -> SIGKILL escalation cascade by
spawning short-lived Python subprocesses.

Marked ``slow`` for the live-subprocess paths so they can be skipped
on the unit-tier critical path; the rest are pure-function tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from tools import _dev_streamer as streamer


class TestPopenKwargs:

    def test_posix_uses_start_new_session(self, monkeypatch):
        monkeypatch.setattr(streamer.os, "name", "posix")
        kwargs = streamer._popen_kwargs_for_signal_propagation()
        assert kwargs == {"start_new_session": True}

    def test_windows_uses_create_new_process_group(self, monkeypatch):
        monkeypatch.setattr(streamer.os, "name", "nt")
        kwargs = streamer._popen_kwargs_for_signal_propagation()
        assert "creationflags" in kwargs
        # CREATE_NEW_PROCESS_GROUP = 0x00000200.
        assert kwargs["creationflags"] & 0x00000200


class TestSendSigintToChild:
    """Pure-mock unit tests for the SIGINT-forwarding helper."""

    def test_returns_false_when_killpg_oserror_posix(self, monkeypatch):
        monkeypatch.setattr(streamer.os, "name", "posix")

        def boom(*args, **kwargs):
            raise OSError("no such process")

        # `os.killpg` / `os.getpgid` only exist on POSIX; on Windows
        # we patch them on for the test.
        monkeypatch.setattr(
            streamer.os, "killpg", boom, raising=False,
        )
        monkeypatch.setattr(
            streamer.os, "getpgid", lambda pid: pid, raising=False,
        )

        class FakeProc:
            pid = 123

        assert streamer._send_sigint_to_child(FakeProc()) is False

    def test_returns_true_on_clean_killpg_posix(self, monkeypatch):
        monkeypatch.setattr(streamer.os, "name", "posix")
        called: list[tuple] = []
        monkeypatch.setattr(
            streamer.os, "killpg",
            lambda pgid, sig: called.append((pgid, sig)),
            raising=False,
        )
        monkeypatch.setattr(
            streamer.os, "getpgid", lambda pid: 999, raising=False,
        )

        class FakeProc:
            pid = 555

        assert streamer._send_sigint_to_child(FakeProc()) is True
        assert called == [(999, streamer.signal.SIGINT)]

    def test_windows_send_signal_ctrl_break(self, monkeypatch):
        monkeypatch.setattr(streamer.os, "name", "nt")
        sent: list[int] = []

        class FakeProc:
            def send_signal(self, sig):
                sent.append(sig)

        # CTRL_BREAK_EVENT lives on the signal module on Windows. On
        # POSIX it's missing — patch it on for the test.
        monkeypatch.setattr(
            streamer.signal, "CTRL_BREAK_EVENT",
            getattr(streamer.signal, "CTRL_BREAK_EVENT", 1),
            raising=False,
        )

        assert streamer._send_sigint_to_child(FakeProc()) is True
        assert sent == [streamer.signal.CTRL_BREAK_EVENT]


class TestTerminateCascade:
    """The escalation cascade with mocked Popen-likes."""

    def test_returns_immediately_on_clean_sigint_exit(
        self, monkeypatch,
    ):
        # Force SIGINT delivery to "succeed", proc.wait returns rc=0.
        monkeypatch.setattr(
            streamer, "_send_sigint_to_child", lambda p: True,
        )

        class FakeProc:
            def wait(self, timeout=None):
                return 0

        rc = streamer._terminate_child(FakeProc(), "[test]")
        assert rc == 0

    def test_falls_through_to_kill_when_all_grace_expires(
        self, monkeypatch,
    ):
        monkeypatch.setattr(
            streamer, "_send_sigint_to_child", lambda p: True,
        )
        # Compress the grace timeouts so the test runs fast.
        monkeypatch.setattr(streamer, "_CHILD_SIGINT_GRACE_SECONDS", 0.01)
        monkeypatch.setattr(streamer, "_CHILD_TERM_GRACE_SECONDS", 0.01)
        monkeypatch.setattr(streamer, "_CHILD_KILL_GRACE_SECONDS", 0.01)

        terminate_calls = []
        kill_calls = []

        class StubbornProc:
            wait_count = 0

            def wait(self, timeout=None):
                # The first two waits time out (sigint, sigterm); the
                # final SIGKILL wait succeeds.
                self.wait_count += 1
                if self.wait_count <= 2:
                    raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
                return -9

            def terminate(self):
                terminate_calls.append(True)

            def kill(self):
                kill_calls.append(True)

        rc = streamer._terminate_child(StubbornProc(), "[stubborn]")
        assert rc == -9
        assert terminate_calls == [True]
        assert kill_calls == [True]

    def test_skip_sigint_step_when_send_fails(self, monkeypatch):
        monkeypatch.setattr(
            streamer, "_send_sigint_to_child", lambda p: False,
        )
        monkeypatch.setattr(streamer, "_CHILD_TERM_GRACE_SECONDS", 0.01)
        terminate_calls = []

        class FakeProc:
            def wait(self, timeout=None):
                return 0

            def terminate(self):
                terminate_calls.append(True)

            def kill(self):
                pass

        rc = streamer._terminate_child(FakeProc(), "[fakeproc]")
        assert rc == 0
        # SIGTERM step ran (since SIGINT-send failed, we skipped to it).
        assert terminate_calls == [True]


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "Live subprocess SIGINT propagation is hard to test reliably on "
        "Windows — CTRL_BREAK_EVENT delivery races console allocation "
        "in pytest's stdio capture. Covered by manual smoke test."
    ),
)
class TestStreamSubprocessLive:
    """Spawn a real Python subprocess; verify Ctrl+C cleanup works
    end-to-end. POSIX-only — Windows is covered by manual smoke."""

    def _spawn_long_lived_python(self) -> list[str]:
        """A child that ignores SIGTERM but honours SIGINT (via the
        default Python KeyboardInterrupt handler)."""
        snippet = (
            "import time, signal, sys\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('child ready', flush=True)\n"
            "try:\n"
            "    time.sleep(60)\n"
            "except KeyboardInterrupt:\n"
            "    print('child caught sigint', flush=True)\n"
            "    sys.exit(0)\n"
        )
        return [sys.executable, "-c", snippet]

    def test_sigint_propagates_and_child_exits_cleanly(self):
        # We run stream_subprocess in a thread and inject a
        # KeyboardInterrupt by signalling the main thread? That's
        # fragile. Instead, we test the `_send_sigint_to_child` path
        # directly against a live spawned child.
        cmd = self._spawn_long_lived_python()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **streamer._popen_kwargs_for_signal_propagation(),
        )
        try:
            # Wait for "child ready" so the child's signal handler is
            # installed before we deliver SIGINT.
            line = proc.stdout.readline()
            assert b"child ready" in line, line

            assert streamer._send_sigint_to_child(proc) is True
            rc = proc.wait(timeout=10.0)
            assert rc == 0, f"child did not exit cleanly on SIGINT (rc={rc})"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)

    def test_terminate_child_exits_within_15_seconds(self):
        """Issue #176 acceptance for layer 2: total escalation must
        complete well within 15s even for a stubborn child."""
        cmd = self._spawn_long_lived_python()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **streamer._popen_kwargs_for_signal_propagation(),
        )
        try:
            line = proc.stdout.readline()
            assert b"child ready" in line, line

            t0 = time.monotonic()
            rc = streamer._terminate_child(proc, "[live]")
            elapsed = time.monotonic() - t0

            assert rc == 0, f"unexpected rc {rc}"
            # The clean SIGINT path returns inside the grace; well
            # under 15s. We give some slack for slow CI.
            assert elapsed < 15.0, (
                f"escalation took {elapsed:.1f}s (must be < 15s)"
            )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)
