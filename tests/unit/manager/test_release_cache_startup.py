# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``workers.release_cache_startup`` (issues #97, #292).

The Blender series fetch contacts download.blender.org.  It must start
only in a process that serves requests (the Waitress serving process,
or ``runserver``'s serving process), never for a management command
such as ``spectacular`` or ``migrate``, never under pytest, and never
with ``SETHLANS_DISABLE_RELEASE_FETCH=1``.

Every test that could start the thread patches ``threading.Thread``, so
no test touches the network.
"""

from __future__ import annotations

import sys

import pytest

from workers import release_cache_startup as startup

MANAGEMENT_COMMANDS = (
    'spectacular', 'migrate', 'makemigrations', 'check', 'collectstatic',
    'shell',
)


@pytest.fixture
def thread_cls(mocker, monkeypatch):
    """A fresh process (nothing started yet) with ``Thread`` mocked out."""
    monkeypatch.setattr(startup, '_started', False)
    return mocker.patch.object(startup.threading, 'Thread')


def _without_pytest(monkeypatch):
    fake_modules = {
        k: v for k, v in sys.modules.items() if not k.startswith('pytest')
    }
    monkeypatch.setattr(sys, 'modules', fake_modules)


class TestFetchDisabled:

    def test_true_under_pytest_without_env(self, monkeypatch):
        """Pytest alone keeps the fetch closed (it does not rely on the
        pytest.ini env flag)."""
        monkeypatch.delenv(startup.DISABLE_ENV, raising=False)
        assert startup.fetch_disabled() is True

    def test_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv(startup.DISABLE_ENV, '1')
        _without_pytest(monkeypatch)
        assert startup.fetch_disabled() is True

    def test_false_when_env_unset_and_pytest_absent(self, monkeypatch):
        monkeypatch.delenv(startup.DISABLE_ENV, raising=False)
        _without_pytest(monkeypatch)
        assert startup.fetch_disabled() is False

    @pytest.mark.parametrize('value', ['true', 'yes', '0', ''])
    def test_only_literal_one_opts_out(self, monkeypatch, value):
        monkeypatch.setenv(startup.DISABLE_ENV, value)
        _without_pytest(monkeypatch)
        assert startup.fetch_disabled() is False


class TestIsRunserverServingProcess:

    def test_reloader_child_serves(self):
        assert startup.is_runserver_serving_process(
            ['manage.py', 'runserver', '8080'], {'RUN_MAIN': 'true'},
        ) is True

    def test_noreload_process_serves(self):
        assert startup.is_runserver_serving_process(
            ['manage.py', 'runserver', '0.0.0.0:8080', '--noreload'], {},
        ) is True

    def test_reloader_parent_does_not_serve(self):
        """The autoreloader's parent only watches files and restarts
        its child; it must not fetch as well."""
        assert startup.is_runserver_serving_process(
            ['manage.py', 'runserver'], {},
        ) is False

    @pytest.mark.parametrize('command', MANAGEMENT_COMMANDS)
    def test_management_command_never_serves(self, command):
        """Even with an inherited ``RUN_MAIN=true`` or a stray
        ``--noreload`` argument, another command stays closed."""
        argv = ['manage.py', command, '--noreload']
        assert startup.is_runserver_serving_process(
            argv, {'RUN_MAIN': 'true'},
        ) is False

    @pytest.mark.parametrize('argv', [[], ['run_manager.py'], ['run_manager.py', '--dev']])
    def test_other_entry_points_are_not_runserver(self, argv):
        assert startup.is_runserver_serving_process(
            argv, {'RUN_MAIN': 'true'},
        ) is False

    def test_reads_process_argv_and_environ_by_default(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['manage.py', 'runserver'])
        monkeypatch.setenv('RUN_MAIN', 'true')
        assert startup.is_runserver_serving_process() is True
        monkeypatch.delenv('RUN_MAIN')
        assert startup.is_runserver_serving_process() is False


class TestStartReleaseCachePopulate:

    def test_starts_one_daemon_thread(self, mocker, thread_cls):
        from workers.utils import blender_series_cache
        mocker.patch.object(startup, 'fetch_disabled', return_value=False)

        assert startup.start_release_cache_populate() is True

        thread_cls.assert_called_once_with(
            target=blender_series_cache.populate_cache,
            name='blender-series-cache-populate',
            daemon=True,
        )
        thread_cls.return_value.start.assert_called_once_with()

    def test_second_call_in_the_same_process_is_a_no_op(self, mocker, thread_cls):
        mocker.patch.object(startup, 'fetch_disabled', return_value=False)

        assert startup.start_release_cache_populate() is True
        assert startup.start_release_cache_populate() is False
        assert thread_cls.call_count == 1

    def test_disabled_starts_nothing(self, mocker, thread_cls):
        mocker.patch.object(startup, 'fetch_disabled', return_value=True)

        assert startup.start_release_cache_populate() is False
        thread_cls.assert_not_called()
        assert startup._started is False

    def test_env_opt_out_wins_outside_pytest(self, monkeypatch, thread_cls):
        """The real gate, pytest hidden: the operator's opt-out alone
        keeps the serving process from fetching."""
        monkeypatch.setenv(startup.DISABLE_ENV, '1')
        _without_pytest(monkeypatch)

        assert startup.start_release_cache_populate() is False
        thread_cls.assert_not_called()

    def test_pytest_guard_wins_without_env(self, monkeypatch, thread_cls):
        monkeypatch.delenv(startup.DISABLE_ENV, raising=False)

        assert startup.start_release_cache_populate() is False
        thread_cls.assert_not_called()


class TestStartIfRunserver:

    def test_starts_in_runserver_serving_process(self, mocker, thread_cls):
        mocker.patch.object(startup, 'is_runserver_serving_process', return_value=True)
        mocker.patch.object(startup, 'fetch_disabled', return_value=False)

        assert startup.start_if_runserver() is True
        thread_cls.assert_called_once()

    def test_does_nothing_elsewhere(self, mocker, thread_cls):
        mocker.patch.object(startup, 'is_runserver_serving_process', return_value=False)
        mocker.patch.object(startup, 'fetch_disabled', return_value=False)

        assert startup.start_if_runserver() is False
        thread_cls.assert_not_called()
