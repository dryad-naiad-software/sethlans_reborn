# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Blender cache runner's round and loop, and the cache-root and build-path checks (BS-MGR-2, -6, -12).

Every step a round or the start calls (the row writes, the claim, the sweep, the thread) is mocked: no
database and no thread. The path checks work on real folders and links under ``tmp_path``.
"""
import threading

import pytest

from workers.services.blender_cache import fetcher, paths, runner, sweep, wanted
from tests.unit.manager._blender_cache_links import make_link

FILE = 'blender-4.5.8-linux-x64.tar.xz'


def test_a_round_requeues_interrupted_fetches_before_it_claims(mocker, tmp_path):
    """BS-MGR-6: a row left FETCHING (both result writes of its fetch failed) is fetched
    again: each round reconciles, moves FETCHING rows back to QUEUED, and only then claims; then sweeps."""
    steps = mocker.Mock()
    steps.claim_next.return_value = None
    mocker.patch.object(runner, 'close_old_connections')
    mocker.patch.object(paths, 'cache_root', return_value=tmp_path)
    for module, name in ((wanted, 'reconcile'), (wanted, 'requeue_interrupted'), (fetcher, 'claim_next'),
                         (sweep, 'sweep')):
        mocker.patch.object(module, name, getattr(steps, name))

    runner._round(threading.Event())

    assert [name for name, *_ in steps.method_calls] == ['reconcile', 'requeue_interrupted', 'claim_next', 'sweep']


def test_the_loop_ends_after_the_round_that_saw_the_stop(mocker):
    """BS-MGR-6: a stop set during a round ends the loop with no 10-minute wait, and the
    thread's database connections are still closed."""
    stop = threading.Event()
    one_round = mocker.patch.object(runner, '_round', side_effect=lambda s: s.set())
    wake = mocker.patch.object(runner, '_wake')
    connections = mocker.patch.object(runner, 'connections')

    runner._loop(stop)

    one_round.assert_called_once_with(stop)
    wake.wait.assert_not_called()
    connections.close_all.assert_called_once_with()


@pytest.mark.parametrize('root, refused', [
    ('data/media/blender', True),
    ('data', True),
    ('data/media-blender', False),
], ids=['inside-media-root', 'containing-media-root', 'sibling-sharing-its-prefix'])
def test_check_root_refuses_a_root_overlapping_media_root(settings, tmp_path, root, refused):
    """BS-MGR-2: ``/media/`` is served to anyone, so a cache root inside ``MEDIA_ROOT`` or containing it is
    refused; a sibling whose name only starts like it is not."""
    settings.MEDIA_ROOT = str(tmp_path / 'data' / 'media')

    if refused:
        with pytest.raises(paths.CacheRootError):
            paths.check_root(tmp_path / root)
    else:
        assert paths.check_root(tmp_path / root) is None


def test_the_runner_does_not_start_on_a_root_inside_media_root(mocker, monkeypatch, settings, tmp_path):
    """BS-MGR-2, BS-MGR-6: with the root inside ``MEDIA_ROOT`` the root check refuses it (the start step
    logs and swallows that), no thread starts and the root is not created."""
    monkeypatch.setattr(runner, '_state', {'thread': None, 'started': False})
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    root = tmp_path / 'media' / 'blender'
    mocker.patch.object(paths, 'cache_root', return_value=root)
    mocker.patch('workers.release_cache_startup.fetch_disabled', return_value=False)
    for name in ('requeue_interrupted', 'requeue_missing_files', 'reconcile'):
        mocker.patch.object(wanted, name)
    threads = mocker.patch.object(runner, 'threading')
    check = mocker.spy(paths, 'check_root')

    assert runner.start_for_server() is False

    assert isinstance(check.spy_exception, paths.CacheRootError)
    threads.Thread.assert_not_called()
    assert not root.exists()


@pytest.mark.parametrize('kind, link_at', [
    (None, None), ('junction', 'version'), ('symlink', 'version'), ('symlink', 'file'),
], ids=['plain-folder-and-file', 'version-junction', 'version-symlink', 'file-symlink'])
def test_build_path_refuses_a_link_below_the_root(tmp_path, kind, link_at):
    """BS-MGR-12: when ``<root>/<version>`` or the file is a link or junction (here to a folder outside the
    root), there is no build path, so nothing is read, served or placed through it; a plain folder and
    file give ``<root>/<version>/<file>``."""
    root, outside = tmp_path / 'blender', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / FILE).write_bytes(b'x')
    if link_at == 'version':
        make_link(kind, root / '4.5.8', outside)
    else:
        (root / '4.5.8').mkdir()
        if link_at == 'file':
            make_link(kind, root / '4.5.8' / FILE, outside / FILE)

    expected = root / '4.5.8' / FILE if kind is None else None
    assert paths.build_path(root, '4.5.8', FILE) == expected
