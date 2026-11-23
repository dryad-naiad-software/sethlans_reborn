# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #295: the reset of interrupted animation videos, without a database.

The gate (management commands never reset; Waitress ``launch()`` and
runserver's serving process do) runs against the test database in
``tests/integration/manager/test_animation_video_startup.py``; the order
inside ``launch()`` is in ``test_run_manager_waitress_launch.py``.  Here:
once per process, the runserver gate, and the database errors of a
database that has not been migrated yet.
"""

import pytest
from django.db import DatabaseError, OperationalError, ProgrammingError

from workers import animation_video_startup as video_startup
from workers.models import Animation


@pytest.fixture(autouse=True)
def _fresh_reset_state(monkeypatch):
    monkeypatch.setattr(video_startup, '_done', False)


@pytest.fixture
def reset(mocker):
    return mocker.patch.object(video_startup, 'reset_interrupted_assemblies', return_value=0)


class TestResetForServer:

    def test_runs_once_per_process(self, reset):
        assert video_startup.reset_for_server() is True
        assert video_startup.reset_for_server() is False
        reset.assert_called_once_with()

    def test_runserver_serving_process_resets(self, mocker, reset):
        gate = mocker.patch.object(video_startup, 'is_runserver_serving_process', return_value=True)
        assert video_startup.reset_if_runserver() is True
        gate.assert_called_once_with()
        reset.assert_called_once_with()

    def test_any_other_process_does_not_reset(self, mocker, reset):
        mocker.patch.object(video_startup, 'is_runserver_serving_process', return_value=False)
        assert video_startup.reset_if_runserver() is False
        reset.assert_not_called()
        # The serving process's own reset is still to come.
        assert video_startup.reset_for_server() is True


class TestResetInterruptedAssemblies:

    @pytest.fixture
    def query(self, mocker):
        return mocker.patch.object(Animation.objects, 'filter')

    @pytest.mark.parametrize('error', [OperationalError, ProgrammingError], ids=['operational', 'programming'])
    def test_unmigrated_database_is_skipped_with_a_warning(self, mocker, query, error):
        """No table or column yet (or a locked database): nothing reset, no raise, a warning logged."""
        query.return_value.update.side_effect = error('no such column: video_status')
        warn = mocker.patch.object(video_startup.logger, 'warning')
        assert video_startup.reset_interrupted_assemblies() == 0
        warn.assert_called_once()
        assert 'no such column: video_status' in str(warn.call_args.args[1])

    def test_other_database_errors_are_not_hidden(self, query):
        query.return_value.update.side_effect = DatabaseError('disk I/O error')
        with pytest.raises(DatabaseError):
            video_startup.reset_interrupted_assemblies()

    def test_marks_assembling_rows_error_and_logs_the_count(self, mocker, query):
        query.return_value.update.return_value = 2
        log = mocker.patch.object(video_startup.logger, 'info')
        assert video_startup.reset_interrupted_assemblies() == 2
        query.assert_called_once_with(video_status='ASSEMBLING')
        query.return_value.update.assert_called_once_with(
            video_status='ERROR', video_error=video_startup.INTERRUPTED_MESSAGE,
        )
        log.assert_called_once()

    def test_nothing_to_reset_logs_nothing(self, mocker, query):
        query.return_value.update.return_value = 0
        log = mocker.patch.object(video_startup.logger, 'info')
        assert video_startup.reset_interrupted_assemblies() == 0
        log.assert_not_called()
