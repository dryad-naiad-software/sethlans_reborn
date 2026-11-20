# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``execute`` paths without processes that hand the job back: local
conflicts, share failures, cancels, shutdown and a missing cache (Unreal
foundation FR-UE-2, FR-UE-17; chunk 7 integration requirements 3-5, 8).
"""
import threading

from sethlans_worker_agent.project_archive.errors import AbortedError, ShareUnavailableError
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled, LocalConflict
from tests.unit.worker.unreal._job_run_world import World


def test_local_conflict_releases(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: (_ for _ in ()).throw(LocalConflict('dispatch_failed', 'port held'))
    result = w.run()
    assert result.released and ('release', 'dispatch_failed') in w.calls


def test_share_failure_releases_and_marks_unreachable(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.share_error = ShareUnavailableError('The archive is not reachable on the Sethlans share.')
    result = w.run()
    assert result.released and w.names() == ['confirm_all', 'unreachable', 'release']
    assert w.calls[2] == ('release', 'share_unavailable')


def test_cancel_causes(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: (_ for _ in ()).throw(Cancelled('canceled'))
    result = w.run()
    assert result.was_canceled and not result.released and 'release' not in w.names()


def test_shutdown_releases_and_skips_finish(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    shutdown = threading.Event()
    shutdown.set()
    w.flow_action = lambda job: (_ for _ in ()).throw(AbortedError('shutdown'))
    result = w.run(shutdown)
    assert result.released and ('release', 'shutdown') in w.calls and 'finish' not in w.names()
    # Review L1: the entry UE may have touched is never trusted again (no slow re-hash in the shutdown budget).
    assert ('quarantine', 'shutdown before the post-job integrity check') in w.calls
    assert 'pristine_integrity=skipped(shutdown; entry quarantined)' in result.stdout


def test_reassigned_reports_nothing(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: (_ for _ in ()).throw(Cancelled('reassigned'))
    result = w.run()
    assert result.released and 'release' not in w.names() and ('finish', False) in w.calls


def test_cache_unavailable_disables_unreal(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    deps = w.deps()
    deps.cache = lambda: None
    w.deps = lambda: deps
    result = w.run()
    assert 'not ready' in result.error_message and w.names()[0] == 'disable'
