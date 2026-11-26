# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The manager's Blender cache: the wanted set, the row reconcile and the sweep (BS-MGR-4, -5, -6, -8).

``wanted`` and ``sweep`` with the model managers mocked (no database). The sweep works on real files
under ``tmp_path``: its job is deleting them.
"""
import os
from datetime import datetime, timezone as dt_timezone

import pytest

from workers.constants import BlenderBuildStatus
from workers.models import BlenderBuild
from workers.services.blender_cache import sweep, wanted
from tests.unit.manager._blender_cache_links import make_link

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def objects(mocker):
    """``BlenderBuild.objects``, mocked."""
    return mocker.patch.object(BlenderBuild, 'objects')


def test_wanted_is_every_supported_version_times_every_reported_platform(mocker):
    """BS-MGR-4 (D7): wanted = resolved versions x non-empty worker platforms; malformed values are left out."""
    supported = mocker.patch.object(wanted, 'SupportedBlenderVersion')
    supported.objects.values_list.return_value = ['4.5.8', '4.2.19', '']
    workers = mocker.patch.object(wanted, 'Worker')
    workers.objects.exclude.return_value.values_list.return_value.distinct.return_value = [
        'linux-x64', 'windows-x64', 'bogus']

    assert wanted.wanted_set() == {
        ('4.5.8', 'linux-x64'), ('4.5.8', 'windows-x64'), ('4.2.19', 'linux-x64'), ('4.2.19', 'windows-x64')}
    workers.objects.exclude.assert_called_once_with(blender_platform='')


def test_reconcile_queues_new_builds_and_deletes_unwanted_rows(mocker, objects):
    """BS-MGR-3a, BS-MGR-5: a new platform (windows-x64) and a new patch (4.5.9) get QUEUED rows through
    ``bulk_create(ignore_conflicts=True)``; the replaced patch's row (4.5.8) is deleted with
    ``filter().delete()``; rows only, the sweep handles the files."""
    mocker.patch.object(wanted, 'wanted_set', return_value={('4.5.9', 'linux-x64'), ('4.5.9', 'windows-x64')})
    objects.values_list.return_value = [('4.5.8', 'linux-x64')]
    objects.filter.return_value.delete.return_value = (1, {})

    assert wanted.reconcile() == (2, 1)

    (rows,), kwargs = objects.bulk_create.call_args
    assert kwargs == {'ignore_conflicts': True}
    assert [(r.version, r.platform, r.file_name, r.status) for r in rows] == [
        ('4.5.9', 'linux-x64', 'blender-4.5.9-linux-x64.tar.xz', BlenderBuildStatus.QUEUED),
        ('4.5.9', 'windows-x64', 'blender-4.5.9-windows-x64.zip', BlenderBuildStatus.QUEUED),
    ]
    objects.filter.assert_called_once_with(version='4.5.8', platform='linux-x64')


def test_fetching_rows_left_by_a_stopped_process_are_queued_again(mocker, objects):
    """BS-MGR-6: at start, every FETCHING row goes back to QUEUED (``update()`` sets ``updated_at``)."""
    mocker.patch.object(wanted.timezone, 'now', return_value=NOW)
    objects.filter.return_value.update.return_value = 2

    assert wanted.requeue_interrupted() == 2

    objects.filter.assert_called_once_with(status=BlenderBuildStatus.FETCHING)
    objects.filter.return_value.update.assert_called_once_with(status=BlenderBuildStatus.QUEUED, updated_at=NOW)


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())


def test_the_sweep_removes_every_file_no_ready_or_fetching_row_names(tmp_path, objects):
    """BS-MGR-8: kept are the READY and FETCHING builds, the FETCHING row's ``.part`` and the release-list
    cache; the removed patch's file goes, and its emptied folder with it."""
    objects.filter.return_value.values_list.return_value = [
        ('4.5.9', 'blender-4.5.9-linux-x64.tar.xz', BlenderBuildStatus.READY),
        ('4.5.9', 'blender-4.5.9-windows-x64.zip', BlenderBuildStatus.FETCHING),
    ]
    kept = ['.part/blender-4.5.9-windows-x64.zip.part', '.release_cache.json.tmp1', '4.5.9/blender-4.5.9-linux-x64.tar.xz',
            'release_cache.json', 'release_cache.json.lock']
    for name in kept + ['4.5.8/blender-4.5.8-linux-x64.tar.xz', '.part/blender-4.5.8-linux-x64.tar.xz.part',
                        '4.5.9/stray.bin']:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b'x')

    assert sweep.sweep(tmp_path) == 3

    assert _files(tmp_path) == kept
    assert not (tmp_path / '4.5.8').exists()
    objects.filter.assert_called_once_with(status__in=(BlenderBuildStatus.READY, BlenderBuildStatus.FETCHING))


@pytest.mark.parametrize('kind, target_name', [
    ('symlink', 'outside'), ('symlink', 'outside/other.bin'), ('junction', 'outside'),
], ids=['folder-link', 'file-link', 'junction'])
def test_the_sweep_removes_a_link_and_keeps_its_target(tmp_path, objects, kind, target_name):
    """BS-MGR-8: a link or junction under the root is removed itself; it is never followed, so what it
    points to (here a file no row names) stays."""
    objects.filter.return_value.values_list.return_value = []
    root, outside = tmp_path / 'blender', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'other.bin').write_bytes(b'keep me')
    make_link(kind, root / '4.5.8', tmp_path / target_name)

    assert sweep.sweep(root) == 1

    assert not os.path.lexists(root / '4.5.8')
    assert (outside / 'other.bin').read_bytes() == b'keep me'
