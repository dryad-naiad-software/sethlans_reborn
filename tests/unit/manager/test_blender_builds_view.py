# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The metadata view's answer for a READY row whose file is missing (BS-MGR-9, BS-MGR-10).

``_ready_answer`` with the file check, the re-queue, the row read and the runner mocked: no database.
The view's routes, auth and the other answers are covered by the integration tests.
"""
import pytest

from workers.constants import BlenderBuildStatus
from workers.models import BlenderBuild
from workers.services.blender_cache import paths, runner, wanted
from workers.views import blender_builds

FILE = 'blender-4.5.8-linux-x64.tar.xz'


def _row(status):
    return BlenderBuild(pk=5, version='4.5.8', platform='linux-x64', file_name=FILE, status=status, size=7)


@pytest.mark.parametrize('row_now, code, state', [
    (None, 404, 'not_wanted'),
    (_row(BlenderBuildStatus.FETCHING), 202, 'fetching'),
], ids=['row-deleted-meanwhile', 'row-claimed-meanwhile'])
def test_a_lost_requeue_answers_from_the_row_as_it_is_now(mocker, row_now, code, state):
    """BS-MGR-9, BS-MGR-10: when the conditional READY -> QUEUED re-queue of a row whose
    file is missing changes nothing (a reconcile deleted the row, or the runner claimed it), the answer
    comes from the row read again; the runner is not woken."""
    mocker.patch.object(paths, 'ready_file', return_value=None)
    requeue = mocker.patch.object(wanted, 'requeue', return_value=False)
    objects = mocker.patch.object(BlenderBuild, 'objects')
    objects.filter.return_value.first.return_value = row_now
    mocker.patch.object(runner, 'is_alive', return_value=True)
    wake = mocker.patch.object(runner, 'wake')

    response = blender_builds._ready_answer(_row(BlenderBuildStatus.READY))

    assert (response.status_code, response.data) == (code, {'status': state})
    requeue.assert_called_once_with(5, BlenderBuildStatus.READY)
    objects.filter.assert_called_once_with(pk=5)
    wake.assert_not_called()
