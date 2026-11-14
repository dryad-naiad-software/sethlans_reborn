# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Farm-private Zen flags and the farm-zenserver identity (FR-UE-23, FR-UE-26; AC-52, Sec R3-W1)."""
import os
import sys

import psutil
import pytest

from sethlans_worker_agent.unreal_executor import zen_private

WORKER_SID = 'S-1-5-21-1-2-3-1001'
OTHER_SID = 'S-1-5-21-1-2-3-1002'
IMAGE = zen_private.ZEN_IMAGE_WINDOWS if sys.platform == 'win32' else zen_private.ZEN_IMAGE_POSIX


class FakeProc:
    def __init__(self, cmdline, name=IMAGE, pid=4242, owner=WORKER_SID, fail=None):
        self.pid, self._cmdline, self._name, self.owner, self._fail = pid, cmdline, name, owner, fail

    def name(self):
        return self._name

    def cmdline(self):
        if self._fail:
            raise self._fail
        return self._cmdline

    def uids(self):
        class U:
            real = os.geteuid() if self.owner == WORKER_SID else -1
        return U()


@pytest.fixture()
def owners(monkeypatch):
    table = {}
    monkeypatch.setattr(zen_private.win32_proc, 'process_owner_sid', lambda pid: table.get(pid, WORKER_SID))
    monkeypatch.setattr(zen_private.owner_only_acl, 'current_user_sid', lambda: WORKER_SID)
    return table


def test_zen_flags_are_the_spike_switches_plus_loopback():
    assert zen_private.zen_flags(r'D:\uecache', 8668) == (
        r'-ZenDataPath=D:\uecache\zen\data', r'-LocalDataCachePath=D:\uecache\zen\fs', '-SharedDataCachePath=None',
        '-ini:Engine:[Zen.AutoLaunch]:DesiredPort=8668,[Zen.AutoLaunch]:RemoteNetworkService=None')


def test_zen_flags_refuse_the_artists_port():
    with pytest.raises(ValueError):
        zen_private.zen_flags(r'D:\uecache', 8558)


@pytest.mark.parametrize('cmdline, expected', [
    (['zenserver.exe', '--port', '8668', '--data-dir', r'D:\uecache\zen\data'], r'D:\uecache\zen\data'),
    (['zenserver.exe', r'--data-dir="D:\uecache\zen\data"'], r'D:\uecache\zen\data'),
    (['zenserver.exe', '--data-dir', '"D:\\uecache\\zen\\data"'], r'D:\uecache\zen\data'),
    (['zenserver.exe', '--port', '8668'], None),
    (['zenserver.exe', '--data-dir'], None),
])
def test_parse_data_dir(cmdline, expected):
    assert zen_private.parse_data_dir(cmdline) == expected


def _cache(tmp_path):
    return str(tmp_path / 'uecache')


def _data(cache, *extra):
    return os.path.join(cache, 'zen', 'data', *extra)


@pytest.mark.parametrize('form', ['plain', 'equals_quoted', 'trailing_separator'])
def test_farm_zenserver_matches(tmp_path, owners, form):
    cache = _cache(tmp_path)
    cmd = {'plain': ['zenserver.exe', '--data-dir', _data(cache)],
           'equals_quoted': ['zenserver.exe', f'--data-dir="{_data(cache)}"'],
           'trailing_separator': ['zenserver.exe', '--data-dir', _data(cache) + os.sep]}[form]
    assert zen_private.is_farm_zenserver(FakeProc(cmd), cache)


def test_short_name_spelling_matches(tmp_path, owners, monkeypatch):
    cache = _cache(tmp_path)
    short = cache.replace('uecache', 'UECACH~1')
    real_long = zen_private.win32_proc.long_path_name
    monkeypatch.setattr(zen_private.win32_proc, 'long_path_name',
                        lambda p: p.replace('UECACH~1', 'uecache') if 'UECACH~1' in p else real_long(p))
    assert zen_private.is_farm_zenserver(FakeProc(['zenserver.exe', '--data-dir', _data(short)]), cache)


@pytest.mark.parametrize('variant', ['uecache2', 'below', 'parent'])
def test_other_data_dirs_do_not_match(tmp_path, owners, variant):
    cache = _cache(tmp_path)
    data_dir = {'uecache2': _data(cache + '2'), 'below': _data(cache, 'x'),
                'parent': os.path.join(cache, 'zen')}[variant]
    assert not zen_private.is_farm_zenserver(FakeProc(['zenserver.exe', '--data-dir', data_dir]), cache)


def test_other_owner_does_not_match(tmp_path, owners):
    cache = _cache(tmp_path)
    owners[4242] = OTHER_SID
    proc = FakeProc(['zenserver.exe', '--data-dir', _data(cache)], owner=OTHER_SID)
    assert not zen_private.is_farm_zenserver(proc, cache)


def test_other_image_does_not_match(tmp_path, owners):
    cache = _cache(tmp_path)
    proc = FakeProc(['x', '--data-dir', _data(cache)], name='zenserver_x.exe')
    assert not zen_private.is_farm_zenserver(proc, cache)


@pytest.mark.parametrize('error', [psutil.AccessDenied(4242), psutil.NoSuchProcess(4242), OSError(5, 'denied')])
def test_unreadable_command_line_is_not_farm(tmp_path, owners, error):
    assert not zen_private.is_farm_zenserver(FakeProc([], fail=error), _cache(tmp_path))


@pytest.mark.skipif(sys.platform != 'win32', reason='owner SIDs are Windows-only')
def test_unreadable_owner_is_not_farm(tmp_path, monkeypatch):
    cache = _cache(tmp_path)

    def denied(pid):
        raise OSError(5, 'access denied')
    monkeypatch.setattr(zen_private.win32_proc, 'process_owner_sid', denied)
    assert not zen_private.is_farm_zenserver(FakeProc(['zenserver.exe', '--data-dir', _data(cache)]), cache)


def test_zenserver_images_are_exact_paths():
    images = zen_private.zenserver_images([r'C:\Program Files\Epic Games\UE_5.8'], r'D:\uecache\ueprofile')
    # Windows image paths compare case-insensitively on every platform (ntpath.normcase).
    assert r'c:\program files\epic games\ue_5.8\engine\binaries\win64\zenserver.exe' in images
    assert r'd:\uecache\ueprofile\profile\appdata\local\unrealengine\common\zen\install\zenserver.exe' in images


def test_ensure_zen_dirs_creates_owned_dirs(tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    zen_private.ensure_zen_dirs(cache)
    assert (cache / 'zen' / 'data').is_dir() and (cache / 'zen' / 'fs').is_dir()
    zen_private.ensure_zen_dirs(cache)  # idempotent
