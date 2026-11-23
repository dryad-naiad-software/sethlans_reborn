# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for ``WorkersConfig.ready`` guards (issues #97, #292).

#97: the Blender series cache populate thread fired unconditionally
during ``WorkersConfig.ready``, so every pytest invocation hit
``download.blender.org``.  #292: ``ready()`` runs for every management
command too (``spectacular``, ``migrate``, ...), and each one started
the fetch.  ``ready()`` now starts it only in ``runserver``'s serving
process; the Waitress serving process starts it from
``waitress_launcher.launch``.  The gate itself is covered in
``test_release_cache_startup.py``.
"""

from __future__ import annotations

import sys

import pytest
from django.apps import apps

from workers import release_cache_startup as startup
from workers.apps import WorkersConfig


# ---------------------------------------------------------------------
# ready() — the release fetch starts for runserver's serving process only.
# ---------------------------------------------------------------------

class TestReadyReleaseFetch:

    @pytest.fixture(autouse=True)
    def _isolated_ready(self, mocker, monkeypatch):
        """Run the real ``ready()`` with its other side effects (logging,
        DB hooks, parts check, DB reset) patched out and ``Thread``
        mocked, so no test touches the network."""
        mocker.patch('sethlans_manager.logging_config.configure')
        mocker.patch('sethlans_manager.db_hooks.register_connection_hooks')
        mocker.patch('workers.services.parts_check.run_parts_check')
        mocker.patch.object(WorkersConfig, '_reset_stuck_assemblies')
        monkeypatch.setattr(startup, '_started', False)
        monkeypatch.delenv('RUN_MAIN', raising=False)
        self.thread_cls = mocker.patch.object(startup.threading, 'Thread')
        self.config = apps.get_app_config('workers')

    @pytest.fixture
    def guard_open(self, mocker):
        """Open the pytest / opt-out guard so only the server gate decides."""
        mocker.patch.object(startup, 'fetch_disabled', return_value=False)

    def _fetch_threads(self):
        return [
            c for c in self.thread_cls.call_args_list
            if c.kwargs.get('name') == startup.THREAD_NAME
        ]

    @pytest.mark.usefixtures('guard_open')
    @pytest.mark.parametrize('argv', [
        ['manage.py', 'spectacular', '--file', 'schema.yml'],
        ['manage.py', 'migrate', '--noinput'],
        ['manage.py', 'makemigrations', '--check'],
        ['manage.py', 'check'],
        ['manage.py', 'collectstatic', '--noinput'],
        ['manage', 'apply_pending_setup', '--data-dir', 'x'],
    ], ids=['spectacular', 'migrate', 'makemigrations', 'check', 'collectstatic', 'apply_pending_setup'])
    def test_management_command_does_not_fetch(self, monkeypatch, argv):
        """#292 reproduction: these commands used to start the fetch."""
        monkeypatch.setattr(sys, 'argv', argv)
        self.config.ready()
        assert self._fetch_threads() == []

    @pytest.mark.usefixtures('guard_open')
    def test_runserver_reloader_parent_does_not_fetch(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['manage.py', 'runserver'])
        self.config.ready()
        assert self._fetch_threads() == []

    @pytest.mark.usefixtures('guard_open')
    def test_runserver_serving_child_fetches(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['manage.py', 'runserver', '8080'])
        monkeypatch.setenv('RUN_MAIN', 'true')
        self.config.ready()
        assert len(self._fetch_threads()) == 1
        self.thread_cls.return_value.start.assert_called_once_with()

    @pytest.mark.usefixtures('guard_open')
    def test_runserver_noreload_fetches(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['manage.py', 'runserver', '--noreload'])
        self.config.ready()
        assert len(self._fetch_threads()) == 1

    @pytest.mark.usefixtures('guard_open')
    def test_server_entry_point_leaves_it_to_the_launcher(self, monkeypatch):
        """``run_manager.py`` boots Django (and so ``ready()``) before
        Waitress starts; the launcher, not ``ready()``, starts the fetch."""
        monkeypatch.setattr(sys, 'argv', ['run_manager.py'])
        self.config.ready()
        assert self._fetch_threads() == []

    def test_env_opt_out_wins_for_runserver(self, monkeypatch):
        """The real guard with pytest hidden: ``SETHLANS_DISABLE_RELEASE_FETCH=1``
        keeps even runserver's serving process from fetching."""
        monkeypatch.setenv(startup.DISABLE_ENV, '1')
        monkeypatch.setattr(sys, 'modules', {
            k: v for k, v in sys.modules.items() if not k.startswith('pytest')
        })
        monkeypatch.setattr(sys, 'argv', ['manage.py', 'runserver', '--noreload'])
        self.config.ready()
        assert self._fetch_threads() == []

    def test_pytest_guard_wins_for_runserver(self, monkeypatch):
        """Without the opt-out, pytest alone keeps runserver's serving
        process from fetching (the #97 guard is kept)."""
        monkeypatch.delenv(startup.DISABLE_ENV, raising=False)
        monkeypatch.setattr(sys, 'argv', ['manage.py', 'runserver', '--noreload'])
        self.config.ready()
        assert self._fetch_threads() == []


# ---------------------------------------------------------------------
# populate_cache logger hardening — regression for I/O errors after
# stdout has closed during teardown.
# ---------------------------------------------------------------------

class TestSafeLog:

    def test_safe_log_swallows_value_error(self, mocker):
        """``ValueError: I/O operation on closed file`` must not leak
        out of ``_safe_log``. The populate thread is a daemon that
        may outlive the stdout handle (e.g. between pytest's session
        teardown and thread exit); raising here would litter stderr
        with scary tracebacks that look like real failures."""
        from workers.utils import blender_series_cache as cache

        mocker.patch.object(
            cache.logger, 'info',
            side_effect=ValueError('I/O operation on closed file.'),
        )
        # Must not raise.
        cache._safe_log('info', 'hello')

    def test_safe_log_swallows_os_error(self, mocker):
        """Same contract for ``OSError`` — stdlib sometimes raises this
        variant when a rotating file handler's target disappears."""
        from workers.utils import blender_series_cache as cache

        mocker.patch.object(
            cache.logger, 'exception',
            side_effect=OSError('stream closed'),
        )
        cache._safe_log('exception', 'boom')

    def test_safe_log_passes_args_through(self, mocker):
        """Printf-style args are forwarded verbatim."""
        from workers.utils import blender_series_cache as cache

        info = mocker.patch.object(cache.logger, 'info')
        cache._safe_log('info', 'populated: %s', ['4.0', '4.1'])
        info.assert_called_once_with('populated: %s', ['4.0', '4.1'])

    def test_safe_log_ignores_unknown_level(self, mocker):
        """``_safe_log`` is intentionally narrow — only 'info' and
        'exception' are wired up today. Unknown levels are silently
        dropped rather than raising, so bugs in the calling code do
        not poison the background thread."""
        from workers.utils import blender_series_cache as cache

        info = mocker.patch.object(cache.logger, 'info')
        exc = mocker.patch.object(cache.logger, 'exception')
        cache._safe_log('warning', 'ignored')
        info.assert_not_called()
        exc.assert_not_called()
