# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #294: the boot-time parts check runs in the serving process only.

``WorkersConfig.ready()`` used to start the parts check in every process
that loaded Django, so a management command (``spectacular``,
``migrate``, ``check``, ...) on a machine without FFmpeg downloaded it.
``ready()`` now starts it for ``runserver``'s serving process alone; the
Waitress serving process starts it from ``waitress_launcher.launch``
(covered in ``test_run_manager_waitress_launch.py``) without waiting for
it: builds stay PENDING while it runs and are queued again when it ends
(an end-to-end case is in ``tests/integration/manager/sequence/
test_video_parts_check_wait.py``).  No test here
touches the network: the FFmpeg download's ``requests.get`` is replaced
by a stand-in that fails the test if it is ever called.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest
from django.apps import apps

from workers import parts_check_startup as startup
from workers.services.parts_check import registry
from workers.services.parts_check.ffmpeg_download_pkg import download

MANAGEMENT_ARGVS = [
    ['manage.py', 'spectacular', '--file', 'schema.yml'],
    ['manage.py', 'migrate', '--noinput'],
    ['manage.py', 'check'],
    ['manage.py', 'collectstatic', '--noinput'],
    ['run_manager.py', '--manage', 'migrate'],
    ['run_manager.py'],
]
MANAGEMENT_IDS = ['spectacular', 'migrate', 'check', 'collectstatic', 'frozen-manage',
                  'run-manager-before-launch']


@pytest.fixture(autouse=True)
def _clean_registry(mocker, monkeypatch):
    """Fresh kickoff state, no real check thread, no network."""
    registry._reset_for_tests()
    monkeypatch.delenv('RUN_MAIN', raising=False)
    mocker.patch.object(download.requests, 'get', side_effect=AssertionError('network call'))
    yield
    registry._reset_for_tests()


@pytest.fixture
def thread_cls(mocker):
    return mocker.patch.object(registry.threading, 'Thread')


@pytest.fixture
def only_part(monkeypatch):
    """Replace the registered parts (FFmpeg) with one test part."""
    def install(check_fn):
        monkeypatch.setattr(registry, '_parts', {'probe': check_fn})
        monkeypatch.setattr(registry, '_statuses', {'probe': registry.Status()})
    return install


def _check_threads(thread_cls):
    return [c for c in thread_cls.call_args_list if c.kwargs.get('name') == 'parts-check']


class TestReadyStartsTheCheckForRunserverOnly:

    @pytest.fixture(autouse=True)
    def _isolated_ready(self, mocker):
        """The real ``ready()`` with its other side effects patched out."""
        mocker.patch('sethlans_manager.logging_config.configure')
        mocker.patch('sethlans_manager.db_hooks.register_connection_hooks')
        mocker.patch('workers.release_cache_startup.start_if_runserver')
        mocker.patch('workers.animation_video_startup.reset_if_runserver')
        self.config = apps.get_app_config('workers')

    @pytest.mark.parametrize('argv', MANAGEMENT_ARGVS, ids=MANAGEMENT_IDS)
    def test_management_command_does_not_run_the_check(self, monkeypatch, thread_cls, argv):
        """#294 reproduction: ``ready()`` started the check (and so the download) here."""
        monkeypatch.setattr(sys, 'argv', argv)
        self.config.ready()
        assert _check_threads(thread_cls) == []
        assert registry._thread_started is False

    def test_pytest_process_does_not_run_the_check(self, thread_cls):
        """The test runner's own argv (whatever it is) is not a serving process."""
        self.config.ready()
        assert _check_threads(thread_cls) == []

    def test_runserver_reloader_parent_does_not_run_the_check(self, monkeypatch, thread_cls):
        monkeypatch.setattr(sys, 'argv', ['manage.py', 'runserver'])
        self.config.ready()
        assert _check_threads(thread_cls) == []

    @pytest.mark.parametrize('argv, run_main', [
        (['manage.py', 'runserver', '8080'], 'true'),
        (['manage.py', 'runserver', '--noreload'], None),
    ], ids=['reloader-child', 'noreload'])
    def test_runserver_serving_process_runs_the_check_once(self, monkeypatch, thread_cls, argv, run_main):
        monkeypatch.setattr(sys, 'argv', argv)
        if run_main:
            monkeypatch.setenv('RUN_MAIN', run_main)
        self.config.ready()
        self.config.ready()  # runserver can fire ready() twice; still one thread
        assert len(_check_threads(thread_cls)) == 1
        thread_cls.return_value.start.assert_called_once_with()


class TestRegistryRunState:
    """``checks_in_progress`` / ``wait_for_checks`` against the real registry."""

    def test_states_before_during_and_after_a_run(self, only_part):
        release = threading.Event()
        only_part(lambda: (release.wait(5), registry.Status(status='ready'))[1])
        assert registry.checks_in_progress() is False
        assert registry.wait_for_checks(None) is False  # never started: returns at once
        startup.start_parts_check()
        assert registry.checks_in_progress() is True
        assert registry.wait_for_checks(0.05) is False
        release.set()
        assert registry.wait_for_checks(5) is True
        assert registry.checks_in_progress() is False
        assert registry.get_status('probe').status == 'ready'

    def test_failing_check_still_ends_the_run(self, only_part):
        def boom():
            raise RuntimeError('broken part')
        only_part(boom)
        startup.start_parts_check()
        assert registry.wait_for_checks(5) is True
        assert registry.get_status('probe').status == 'failed'

    def test_second_start_runs_no_second_check(self, only_part):
        calls = []
        only_part(lambda: (calls.append(1), registry.Status(status='ready'))[1])
        startup.start_parts_check()
        startup.start_parts_check()
        assert registry.wait_for_checks(5) is True
        assert calls == [1]

    def test_reset_is_not_undone_by_an_old_run(self, only_part):
        """A run that ends after a test reset sets its own Event, not the new run's."""
        release = threading.Event()
        only_part(lambda: (release.wait(5), registry.Status(status='ready'))[1])
        startup.start_parts_check()
        old_done = registry._checks_done
        registry._reset_for_tests()
        startup.start_parts_check()  # a fresh run, blocked on the same release
        new_done = registry._checks_done
        assert new_done is not old_done
        release.set()
        assert old_done.wait(5) and new_done.wait(5)


class TestStartForServer:
    """The Waitress serving process's hook: no wait, re-queue when the check ends."""

    @pytest.fixture(autouse=True)
    def _queue(self, mocker):
        self.accepting = mocker.patch('workers.video_queue.is_accepting', return_value=True)
        self.queue_pending = mocker.patch('workers.video_startup.queue_pending', return_value=1)
        self.connections = mocker.patch.object(startup, 'connections')

    @staticmethod
    def _join_requeue_thread():
        for thread in threading.enumerate():
            if thread.name == startup.REQUEUE_THREAD_NAME:
                thread.join(5)
                assert not thread.is_alive()

    def test_returns_at_once_and_requeues_after_a_slow_check(self, only_part):
        release = threading.Event()
        only_part(lambda: (release.wait(5), registry.Status(status='ready'))[1])
        started = time.monotonic()
        startup.start_for_server()
        assert time.monotonic() - started < 1.0  # no wait for the check
        assert registry.checks_in_progress() is True
        self.queue_pending.assert_not_called()
        release.set()
        self._join_requeue_thread()
        self.queue_pending.assert_called_once_with()
        self.connections.close_all.assert_called_once_with()

    def test_no_requeue_when_the_queue_is_not_accepting(self, only_part):
        """The check ended before the start hook; the hook's own queue_pending covers it."""
        self.accepting.return_value = False
        only_part(lambda: registry.Status(status='ready'))
        startup.start_for_server()
        self._join_requeue_thread()
        self.queue_pending.assert_not_called()

    def test_a_failing_requeue_is_logged_not_raised(self, only_part, mocker):
        self.queue_pending.side_effect = RuntimeError('db down')
        log = mocker.patch.object(startup.logger, 'exception')
        only_part(lambda: registry.Status(status='ready'))
        startup.start_for_server()
        self._join_requeue_thread()
        log.assert_called_once()
        self.connections.close_all.assert_called_once_with()


class TestBuildWaitsForTheCheck:
    """``video_sequence.run_task`` claims nothing while the check runs."""

    def test_no_claim_while_the_check_runs(self, mocker):
        from workers import video_sequence
        mocker.patch('workers.services.parts_check.checks_in_progress', return_value=True)
        claim = mocker.patch.object(video_sequence, 'claim')
        assert video_sequence.run_task((1, None, 'video'), 'boot') is False
        claim.assert_not_called()

    def test_claims_once_the_check_is_over(self, mocker):
        from workers import video_sequence
        mocker.patch('workers.services.parts_check.checks_in_progress', return_value=False)
        claim = mocker.patch.object(video_sequence, 'claim', return_value=False)
        assert video_sequence.run_task((1, None, 'video'), 'boot') is False
        claim.assert_called_once_with((1, None, 'video'), 'boot')
