# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`sethlans_worker_agent.caddy_supervisor` (Phase 5b).

Covers:

* Happy path — ``start()`` templates the Caddyfile, writes it
  atomically, and spawns Caddy with list-form argv (never
  ``shell=True``).
* ``stop()`` sends the platform-appropriate graceful signal, waits
  with the configured timeout, and escalates to kill on timeout.
* Watchdog crash detection → restart with 1 s backoff, up to 3
  attempts. Restart budget exhaustion sets ``error_event``.
* Restart budget reset after 60 s of stable uptime.
* Orphan prevention: POSIX ``setsid`` preexec_fn passed to Popen;
  Windows ``CREATE_NEW_PROCESS_GROUP`` flag passed.
* Input validation: binary path missing → ``CaddyBinaryNotFoundError``.
* Atomic Caddyfile write: non-empty content only.

Every test mocks ``subprocess.Popen`` — no real Caddy is spawned.
"""

from __future__ import annotations

import time as _time
from unittest.mock import patch

import pytest

from shared.caddy_supervisor import (
    CaddyBinaryNotFoundError,
    atomic_write_text,
)
from shared.caddy_supervisor import supervisor as sup_mod
from tests.unit.worker._caddy_supervisor_helpers import (
    FakeProc, make_supervisor, make_worker_tree,
)


@pytest.fixture
def worker_tree(tmp_path):
    return make_worker_tree(tmp_path)


# ---------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------

def test_start_raises_if_binary_missing(worker_tree):
    worker_tree.binary.unlink()
    sv = make_supervisor(worker_tree)
    with pytest.raises(CaddyBinaryNotFoundError):
        sv.start()


def test_atomic_write_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        atomic_write_text(tmp_path / 'Caddyfile', '')


def test_atomic_write_writes_content_and_creates_parent(tmp_path):
    target = tmp_path / 'nested' / 'Caddyfile'
    atomic_write_text(target, 'abc')
    assert target.read_text() == 'abc'
    # No tempfile crumbs left behind.
    leftover = [p for p in target.parent.iterdir() if p != target]
    assert leftover == []


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------

def test_start_templates_caddyfile_and_spawns_listform(worker_tree):
    sv = make_supervisor(worker_tree)
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured['argv'] = argv
        captured['kwargs'] = kwargs
        return FakeProc()

    with patch.object(
        sup_mod._proc.subprocess, 'Popen', side_effect=fake_popen,
    ) as popen:
        sv.start()
        assert isinstance(captured['argv'], list)
        assert captured['argv'][0] == str(worker_tree.binary)
        assert '--config' in captured['argv']
        assert str(worker_tree.caddyfile) in captured['argv']
        # No shell=True passed to Popen.
        assert captured['kwargs'].get('shell', False) is not True
        popen.assert_called_once()
        # Caddyfile was templated + atomically written.
        assert worker_tree.caddyfile.is_file()
        content = worker_tree.caddyfile.read_text()
        assert ':8443 {' in content
        assert '127.0.0.1:18443' in content
    sv.stop(timeout=0.1)


def test_start_platform_flags_posix(worker_tree):
    sv = make_supervisor(worker_tree)
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured['kwargs'] = kwargs
        return FakeProc()

    with patch.object(
        sup_mod._proc.platform, 'system', return_value='Linux',
    ):
        with patch.object(
            sup_mod._proc.subprocess, 'Popen', side_effect=fake_popen,
        ):
            sv.start()
    sv.stop(timeout=0.1)
    assert 'preexec_fn' in captured['kwargs']
    assert callable(captured['kwargs']['preexec_fn'])
    assert 'creationflags' not in captured['kwargs']


def test_start_platform_flags_windows(worker_tree):
    sv = make_supervisor(worker_tree)
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured['kwargs'] = kwargs
        return FakeProc()

    with patch.object(
        sup_mod._proc.platform, 'system', return_value='Windows',
    ):
        with patch.object(
            sup_mod._proc.subprocess, 'Popen', side_effect=fake_popen,
        ):
            sv.start()
    sv.stop(timeout=0.1)
    assert 'creationflags' in captured['kwargs']
    assert captured['kwargs']['creationflags'] != 0
    assert 'preexec_fn' not in captured['kwargs']


# ---------------------------------------------------------------------
# Graceful stop
# ---------------------------------------------------------------------

def test_stop_sends_sigterm_on_posix_and_waits(worker_tree):
    sv = make_supervisor(worker_tree)
    fake = FakeProc()
    with patch.object(
        sup_mod._proc.subprocess, 'Popen', return_value=fake,
    ):
        sv.start()

    # Simulate Caddy exiting cleanly in response to the graceful signal.
    def killpg_side_effect(pgid, sig):
        fake.set_exit(0)

    # ``os.killpg``/``os.getpgid`` don't exist on Windows, so patch
    # with ``create=True`` to make the tests cross-platform.
    with patch.object(
        sup_mod._proc.platform, 'system', return_value='Linux',
    ), patch.object(
        sup_mod._proc.os, 'killpg', side_effect=killpg_side_effect,
        create=True,
    ) as killpg, patch.object(
        sup_mod._proc.os, 'getpgid', return_value=fake.pid, create=True,
    ):
        sv.stop(timeout=1.0)
    assert killpg.called
    assert fake.poll() == 0


def test_stop_escalates_to_kill_on_timeout(worker_tree):
    sv = make_supervisor(worker_tree)
    fake = FakeProc()
    with patch.object(
        sup_mod._proc.subprocess, 'Popen', return_value=fake,
    ):
        sv.start()
    # Leave exit_code=None so the first wait() times out.
    with patch.object(
        sup_mod._proc.platform, 'system', return_value='Windows',
    ):
        sv.stop(timeout=0.05)
    assert fake.killed is True


# ---------------------------------------------------------------------
# Watchdog crash/restart behaviour
# ---------------------------------------------------------------------

def test_watchdog_restarts_up_to_budget_then_sets_error(worker_tree):
    """After MAX_RESTART_ATTEMPTS crashes in a row, error_event fires."""
    sv = make_supervisor(worker_tree)
    procs: list[FakeProc] = []

    def fake_popen(argv, **kwargs):
        p = FakeProc(exit_code=1)  # already dead
        procs.append(p)
        return p

    with patch.object(sup_mod, 'WATCHDOG_POLL_SECONDS', 0.01), \
            patch.object(sup_mod, 'RESTART_BACKOFF_SECONDS', 0.01), \
            patch.object(
                sup_mod._proc.subprocess, 'Popen',
                side_effect=fake_popen,
            ):
        sv.start()
        assert sv.error_event.wait(timeout=3.0)
    # 1 initial spawn + 3 restart attempts = 4 total.
    assert len(procs) == 4
    sv.stop(timeout=0.1)


def test_watchdog_resets_counter_after_stable_uptime(worker_tree):
    """After stable uptime, a subsequent crash does not exhaust the budget."""
    sv = make_supervisor(worker_tree)
    live_proc = FakeProc(exit_code=None)
    sequence = [FakeProc(exit_code=1), FakeProc(exit_code=1), live_proc]
    idx = {'i': 0}

    def fake_popen(argv, **kwargs):
        p = sequence[min(idx['i'], len(sequence) - 1)]
        idx['i'] += 1
        return p

    with patch.object(sup_mod, 'WATCHDOG_POLL_SECONDS', 0.01), \
            patch.object(sup_mod, 'RESTART_BACKOFF_SECONDS', 0.01), \
            patch.object(sup_mod, 'STABLE_UPTIME_RESET_SECONDS', 0.1), \
            patch.object(
                sup_mod._proc.subprocess, 'Popen',
                side_effect=fake_popen,
            ):
        sv.start()
        deadline = _time.monotonic() + 3.0
        while _time.monotonic() < deadline and idx['i'] < 3:
            _time.sleep(0.05)
        # Let watchdog observe stable uptime and reset the counter.
        _time.sleep(0.5)
    assert not sv.error_event.is_set()
    live_proc.set_exit(0)
    sv.stop(timeout=0.5)


# ---------------------------------------------------------------------
# is_running diagnostic
# ---------------------------------------------------------------------

def test_is_running_reports_process_state(worker_tree):
    sv = make_supervisor(worker_tree)
    assert sv.is_running() is False
    fake = FakeProc()
    with patch.object(
        sup_mod._proc.subprocess, 'Popen', return_value=fake,
    ):
        sv.start()
    assert sv.is_running() is True
    fake.set_exit(0)
    sv.stop(timeout=0.5)
    assert sv.is_running() is False
