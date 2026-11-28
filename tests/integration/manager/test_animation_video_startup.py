# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #295: interrupted animation videos are reset by the serving process only.

``WorkersConfig.ready()`` used to mark every animation ``ASSEMBLING`` as
``ERROR`` in every process that loaded Django, so a management command
(``check``, ``migrate``, ...) run beside a live server marked the
server's running video builds failed.  The reset now runs once in the
Waitress serving process, from ``waitress_launcher.launch`` before the
listeners start, and in runserver's serving process; never for a
management command.  These tests run the real ``ready()`` and the real
``launch()`` (with fake listeners) against the test database.
"""

import sys

import pytest
from django.apps import apps

from sethlans_manager import waitress_launcher
from workers import animation_video_startup as video_startup
from workers.models import Animation

MANAGEMENT_ARGVS = [
    ['manage.py', 'check'],
    ['manage.py', 'migrate', '--noinput'],
    ['manage.py', 'spectacular', '--file', 'schema.yml'],
    ['manage.py', 'collectstatic', '--noinput'],
    ['run_manager.py', '--manage', 'migrate'],
    ['run_manager.py'],
    ['manage.py', 'runserver'],
]
MANAGEMENT_IDS = ['check', 'migrate', 'spectacular', 'collectstatic', 'frozen-manage',
                  'run-manager-before-launch', 'runserver-reloader-parent']


@pytest.fixture(autouse=True)
def _fresh_reset_state(monkeypatch):
    """Each test is a fresh process: the once-per-process reset has not run."""
    monkeypatch.setattr(video_startup, '_done', False)
    monkeypatch.delenv('RUN_MAIN', raising=False)


@pytest.fixture
def assembling(project, asset):
    """An animation whose video a live server is building right now."""
    return Animation.objects.create(
        name='LiveBuild', project=project, asset=asset,
        output_file_pattern='frame_####.png', start_frame=1, end_frame=2, frame_step=1,
        video_settings={'preset': 'web_h264', 'framerate': 24},
        video_status='ASSEMBLING', video_error='',
    )


def _video_state(animation):
    animation.refresh_from_db()
    return animation.video_status, animation.video_error


class TestReadyResetsForRunserverOnly:

    @pytest.fixture(autouse=True)
    def _isolated_ready(self, mocker):
        """The real ``ready()`` with its other side effects patched out."""
        mocker.patch('sethlans_manager.logging_config.configure')
        mocker.patch('sethlans_manager.db_hooks.register_connection_hooks')
        mocker.patch('workers.release_cache_startup.start_if_runserver')
        mocker.patch('workers.parts_check_startup.start_if_runserver')
        self.config = apps.get_app_config('workers')

    @pytest.mark.parametrize('argv', MANAGEMENT_ARGVS, ids=MANAGEMENT_IDS)
    def test_management_command_leaves_a_live_build_alone(self, monkeypatch, assembling, argv):
        """#295 reproduction: ``ready()`` marked this build ERROR."""
        monkeypatch.setattr(sys, 'argv', argv)
        self.config.ready()
        assert _video_state(assembling) == ('ASSEMBLING', '')

    def test_test_runner_leaves_a_live_build_alone(self, assembling):
        self.config.ready()
        assert _video_state(assembling) == ('ASSEMBLING', '')

    @pytest.mark.parametrize('argv, run_main', [
        (['manage.py', 'runserver', '8080'], 'true'),
        (['manage.py', 'runserver', '--noreload'], None),
    ], ids=['reloader-child', 'noreload'])
    def test_runserver_serving_process_resets_once(self, monkeypatch, assembling, argv, run_main):
        monkeypatch.setattr(sys, 'argv', argv)
        if run_main:
            monkeypatch.setenv('RUN_MAIN', run_main)
        self.config.ready()
        assert _video_state(assembling) == ('ERROR', video_startup.INTERRUPTED_MESSAGE)
        # A build this process starts afterwards is not reset by a second ready().
        Animation.objects.filter(pk=assembling.pk).update(video_status='ASSEMBLING', video_error='')
        self.config.ready()
        assert _video_state(assembling) == ('ASSEMBLING', '')


class TestWaitressLaunchResets:

    @pytest.fixture(autouse=True)
    def _fake_listeners(self, mocker):
        """Real ``launch()``; fake servers and threads, other start steps patched."""
        waitress_launcher._servers.clear()
        waitress_launcher._threads.clear()
        waitress_launcher._shutdown_event.clear()
        mocker.patch.object(waitress_launcher, 'limit_request_bodies')
        mocker.patch.object(waitress_launcher, 'get_waitress_tuning', return_value={})
        mocker.patch.object(waitress_launcher, '_install_signal_handlers')
        mocker.patch.object(waitress_launcher.waitress, 'create_server', side_effect=lambda *a, **k: mocker.MagicMock())
        mocker.patch('sethlans_manager.wsgi.application', new=object())
        mocker.patch('workers.parts_check_startup.start_for_server')
        mocker.patch('workers.video_startup.on_server_start')
        mocker.patch('workers.release_cache_startup.start_release_cache_populate')
        mocker.patch('workers.services.blender_cache.runner.start_for_server')  # Blender sources BS-MGR-5/6
        self.thread = mocker.MagicMock()
        self.thread.is_alive.return_value = False
        mocker.patch.object(waitress_launcher.threading, 'Thread', return_value=self.thread)
        yield
        waitress_launcher._servers.clear()
        waitress_launcher._threads.clear()
        waitress_launcher._shutdown_event.clear()

    def _launch(self, tmp_path):
        waitress_launcher._shutdown_event.set()
        waitress_launcher.launch(public_port=8090, internal_port=8088, ini_path=tmp_path / 'manager.ini')

    def test_reset_is_done_before_the_listeners_start(self, assembling, tmp_path):
        seen_by_listener = []
        self.thread.start.side_effect = lambda: seen_by_listener.append(_video_state(assembling))
        self._launch(tmp_path)
        expected = ('ERROR', video_startup.INTERRUPTED_MESSAGE)
        assert seen_by_listener == [expected, expected]

    def test_other_video_states_are_untouched(self, assembling, tmp_path):
        Animation.objects.filter(pk=assembling.pk).update(video_status='DONE')
        self._launch(tmp_path)
        assert _video_state(assembling) == ('DONE', '')
