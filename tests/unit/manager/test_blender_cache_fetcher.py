# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The manager's Blender cache: claim, one fetch, the disk floor and the runner (BS-MGR-3a, -6, -7).

``fetcher`` and ``runner`` with ``BlenderBuild.objects``, the release list, the download and the disk check
mocked (no database, no network). The fetch places real files under ``tmp_path``.
"""
import hashlib
import os
import threading
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest

from shared.blender_sources import BlenderSourcesError, BuildEntry, DownloadStoppedError
from workers.constants import BlenderBuildStatus
from workers.models import BlenderBuild
from workers.services.blender_cache import fetcher, runner, wanted

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=dt_timezone.utc)
GIB = 1024 ** 3
FILE = 'blender-4.5.8-linux-x64.tar.xz'
BODY = b'archive bytes'
BODY_SHA = hashlib.sha256(BODY).hexdigest()


@pytest.fixture
def objects(mocker):
    mocker.patch.object(fetcher.timezone, 'now', return_value=NOW)
    return mocker.patch.object(BlenderBuild, 'objects')


def _row(status=BlenderBuildStatus.FETCHING):
    return BlenderBuild(pk=5, version='4.5.8', platform='linux-x64', file_name=FILE, status=status)


@pytest.mark.parametrize('changed', [1, 0], ids=['claimed', 'claimed-elsewhere'])
def test_only_a_claim_that_changed_the_row_fetches(objects, changed):
    """BS-MGR-3a, BS-MGR-6: the claim is one conditional ``update()`` QUEUED -> FETCHING; when it changes
    no row (another claim took it), nothing is claimed."""
    due = objects.filter.return_value  # filter(due_q) and filter(pk=..., status=...) alike
    due.order_by.return_value.__getitem__.return_value = [_row(BlenderBuildStatus.QUEUED)]
    due.update.return_value = changed

    row = fetcher.claim_next()

    assert objects.filter.call_args.kwargs == {'pk': 5, 'status': BlenderBuildStatus.QUEUED}
    due.update.assert_called_once_with(status=BlenderBuildStatus.FETCHING, error='', updated_at=NOW)
    if changed:
        assert (row.pk, row.status) == (5, BlenderBuildStatus.FETCHING)
    else:
        assert row is None


@pytest.fixture
def fetch_env(monkeypatch, mocker, tmp_path):
    """Plenty of disk; the release list names the row's file; the download writes it under ``.part``."""
    env = SimpleNamespace(root=tmp_path / 'blender', free=10 * GIB, downloads=[])
    monkeypatch.setattr(fetcher, 'shutil', SimpleNamespace(disk_usage=lambda _root: SimpleNamespace(free=env.free)))
    env.find = mocker.patch.object(fetcher, 'find_build', return_value=BuildEntry(
        '4.5.8', 'linux-x64', FILE, 'AB' * 32, 'https://mirror.test/release/'))

    def download(entry, dest_dir, stop=None):
        env.downloads.append((entry.file, dest_dir, stop))
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / entry.file).write_bytes(BODY)
        return dest_dir / entry.file
    monkeypatch.setattr(fetcher, 'download_build', download)
    return env


@pytest.mark.parametrize('changed', [1, 0], ids=['ready', 'row-gone-during-fetch'])
def test_a_fetch_places_the_file_and_marks_the_row_ready(objects, fetch_env, changed):
    """BS-MGR-3a, BS-MGR-6: the build goes through ``<root>/.part/`` into ``<root>/<version>/``, then one
    conditional ``update()`` FETCHING -> READY. When that changes no row (deleted or changed meanwhile), the
    row is not written again (it does not come back) and the placed file is removed."""
    objects.filter.return_value.update.return_value = changed
    stop = object()

    fetcher.fetch(_row(), fetch_env.root, stop)

    assert fetch_env.downloads == [(FILE, fetch_env.root / '.part', stop)]
    objects.filter.assert_called_once_with(pk=5, status=BlenderBuildStatus.FETCHING)
    objects.filter.return_value.update.assert_called_once_with(
        status=BlenderBuildStatus.READY, sha256='ab' * 32, size=len(BODY), source='https://mirror.test/release/',
        error='', next_try_at=None, updated_at=NOW)
    assert [name for name, *_ in objects.method_calls] == ['filter']
    assert (fetch_env.root / '4.5.8' / FILE).exists() is bool(changed)
    assert not (fetch_env.root / '.part' / FILE).exists()


@pytest.mark.parametrize('error, text', [
    (BlenderSourcesError('Blender 4.5.8 linux-x64: every source failed: a.test: HTTP 403'),
     'Blender 4.5.8 linux-x64: every source failed: a.test: HTTP 403'),
    (RuntimeError('boom'), 'RuntimeError: boom'),
], ids=['all-sources-failed', 'unexpected-exception'])
def test_a_failed_fetch_leaves_the_row_failed_for_30_minutes(objects, fetch_env, error, text):
    """BS-MGR-6: every exception of one fetch is caught (the runner keeps going); the row goes FETCHING ->
    FAILED with the error text and ``next_try_at`` = now + 30 min."""
    fetch_env.find.side_effect = error
    objects.filter.return_value.update.return_value = 1

    fetcher.fetch(_row(), fetch_env.root, None)

    objects.filter.assert_called_once_with(pk=5, status=BlenderBuildStatus.FETCHING)
    objects.filter.return_value.update.assert_called_once_with(
        status=BlenderBuildStatus.FAILED, error=text, next_try_at=NOW + timedelta(minutes=30), updated_at=NOW)


@pytest.mark.parametrize('free, refusal', [
    (int(1.5 * GIB), 'not enough disk space: 1.5 GiB free, 2 GiB needed'),
    (2 * GIB, None),
], ids=['1.5-GiB-refused', '2-GiB-fetches'])
def test_the_disk_floor_refuses_with_no_request(objects, fetch_env, free, refusal):
    """BS-MGR-7: with less than 2 GiB free under the root the row fails with the message and no request
    is sent; 2 GiB is enough."""
    fetch_env.free = free
    objects.filter.return_value.update.return_value = 1

    fetcher.fetch(_row(), fetch_env.root, None)

    if refusal is None:
        assert fetch_env.find.call_count == 1
        return
    fetch_env.find.assert_not_called()
    assert fetch_env.downloads == []
    assert objects.filter.return_value.update.call_args.kwargs['error'] == refusal


def _in_place(root, data):
    final = root / '4.5.8' / FILE
    final.parent.mkdir(parents=True)
    final.write_bytes(data)
    return final


@pytest.mark.parametrize('existing, downloads', [(BODY, 0), (b'older bytes', 1)],
                         ids=['right-sha256-kept', 'other-bytes-replaced'])
def test_a_file_already_in_place_is_kept_only_with_the_listed_sha256(objects, fetch_env, existing, downloads):
    """BS-MGR-6: a regular file already at ``<root>/<version>/<file>`` whose SHA-256 is
    the release list's (listed in upper case here) is kept with no download (a crash between ``os.replace``
    and the success write); other bytes are downloaded again and replaced. Either way the row goes READY
    with the lower-case SHA-256."""
    fetch_env.find.return_value = BuildEntry('4.5.8', 'linux-x64', FILE, BODY_SHA.upper(), 'https://m.test/')
    final = _in_place(fetch_env.root, existing)
    objects.filter.return_value.update.return_value = 1

    fetcher.fetch(_row(), fetch_env.root, None)

    assert len(fetch_env.downloads) == downloads
    assert final.read_bytes() == BODY
    ready = objects.filter.return_value.update.call_args.kwargs
    assert (ready['status'], ready['sha256'], ready['size']) == (BlenderBuildStatus.READY, BODY_SHA, len(BODY))


def test_a_link_to_the_right_bytes_does_not_count_as_the_build_in_place(tmp_path):
    """BS-MGR-6, BS-MGR-12: only a regular file at the final path can be kept as the build; a link to a file
    with the right bytes never counts."""
    final = tmp_path / FILE
    (tmp_path / 'right.bin').write_bytes(BODY)
    try:
        os.symlink(tmp_path / 'right.bin', final)
    except OSError as exc:
        pytest.skip(f"no symlink rights here: {exc}")

    assert fetcher._matches(final, BODY_SHA, None) is False


@pytest.mark.parametrize('where', ['while-checking-the-placed-file', 'during-the-download'])
def test_a_stop_puts_the_row_back_to_queued(objects, fetch_env, mocker, where):
    """BS-MGR-6: a stop (server shutdown) while hashing a file already in place, or during the download,
    moves the row FETCHING -> QUEUED through the conditional re-queue; no failure is written."""
    requeue = mocker.patch.object(wanted, 'requeue', return_value=True)
    download = mocker.patch.object(fetcher, 'download_build', side_effect=DownloadStoppedError('stopped'))
    stop = threading.Event()
    if where == 'while-checking-the-placed-file':
        _in_place(fetch_env.root, b'older bytes')
        stop.set()

    fetcher.fetch(_row(), fetch_env.root, stop)

    requeue.assert_called_once_with(5, BlenderBuildStatus.FETCHING)
    assert download.call_count == (where == 'during-the-download')
    objects.filter.assert_not_called()


def test_an_exception_escaping_one_fetch_does_not_end_the_runner(mocker, tmp_path):
    """BS-MGR-6: the runner fetches one claimed row at a time and goes on to the next when one fetch raises."""
    mocker.patch.object(runner, 'close_old_connections')
    rows = [_row(), BlenderBuild(pk=6, version='4.5.8', platform='windows-x64', file_name='x.zip')]
    mocker.patch.object(fetcher, 'claim_next', side_effect=rows + [None])
    fetch = mocker.patch.object(fetcher, 'fetch', side_effect=[RuntimeError('boom'), None])
    stop = SimpleNamespace(is_set=lambda: False)

    runner._fetch_due(tmp_path, stop)

    assert [c.args[0].pk for c in fetch.call_args_list] == [5, 6]


def test_the_start_steps_requeue_before_the_runner_starts(mocker, monkeypatch):
    """BS-MGR-6, BS-MGR-9, BS-MGR-5: at serving-process start, interrupted fetches and builds missing on
    disk are re-queued and the rows reconciled before the runner starts; a failing step does not stop the
    others; a second call does nothing."""
    monkeypatch.setattr(runner, '_state', {'thread': None, 'started': False})
    steps = mocker.Mock()
    steps.requeue_interrupted.side_effect = RuntimeError('database locked')
    steps.start.return_value = True
    mocker.patch.object(wanted, 'requeue_interrupted', steps.requeue_interrupted)
    mocker.patch.object(wanted, 'requeue_missing_files', steps.requeue_missing_files)
    mocker.patch.object(wanted, 'reconcile', steps.reconcile)
    mocker.patch.object(runner, '_start_thread', steps.start)

    assert runner.start_for_server() is True
    assert runner.start_for_server() is False

    assert [name for name, *_ in steps.method_calls] == [
        'requeue_interrupted', 'requeue_missing_files', 'reconcile', 'start']
