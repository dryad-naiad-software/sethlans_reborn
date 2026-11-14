# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Farm-profile config check, CVar layer counts (FR-UE-27, FR-UE-22; AC-53) and housekeeping."""
import os
import sys
import time

import pytest

from sethlans_worker_agent.unreal_executor import profile_config, profile_housekeeping, user_state


@pytest.fixture()
def profile(tmp_path):
    path = tmp_path / 'ueprofile'
    user_state.ensure_profile(path)
    (tmp_path / 'trash').mkdir()
    return path


def layer(profile, index):
    path = profile_config.config_layers(profile)[index]
    path.mkdir(parents=True, exist_ok=True)
    return path


def scan(profile, merged=()):
    return profile_config.scan_farm_profile_config(profile, profile.parent / 'trash', merged_names=merged)


def test_clean_profile_is_untouched(profile):
    ini = layer(profile, 0) / 'WindowsEditor' / 'EditorSettings.ini'
    ini.parent.mkdir()
    ini.write_text('[/Script/UnrealEd.EditorSettings]\nbLoadTheMostRecentlyLoadedProjectAtStartup=False\n')
    result = scan(profile)
    assert result.ok and result.reset == 0 and result.user_cvars == 0
    assert ini.exists()


@pytest.mark.parametrize('index, text', [
    (0, '[Zen.AutoLaunch]\nDataPath=C:\\elsewhere\n'),
    (0, '[ConsoleVariables]\nr.Streaming.PoolSize=100\n'),
    (1, '[Core.Log]\nGlobal=Log\n'),
    (0, '[SystemSettings]\n+r.Foo=1\n'),
    (2, '; an empty user layer file\n'),
    (0, '[/Script/UnrealEd.EditorSettings]\nGlobalLocalDDCPath=(Path="D:\\ddc")\n'),
])
def test_a_hit_resets_all_three_layers(profile, index, text):
    target = layer(profile, index) / 'User.ini'
    target.write_text(text)
    keep = layer(profile, (index + 1) % 3) / 'Other.ini'
    keep.write_text('[Core.Log]\nGlobal=Log\n')
    result = scan(profile, merged=['r.Streaming.PoolSize'])
    assert result.ok and result.reset == 1 and result.user_cvars == 0 and result.findings
    for path in profile_config.config_layers(profile):
        assert path.is_dir() and list(path.iterdir()) == []
    if index == 0 and 'PoolSize' in text:
        assert result.found_cvars == 1 and result.collisions == ('r.Streaming.PoolSize',)


def test_undecodable_file_resets(profile):
    (layer(profile, 0) / 'Bad.ini').write_bytes(b'\xff\xfe\x00\xd8broken')
    (layer(profile, 1) / 'Break.ini').write_text('[Core]\nA=1\x0bB=2\n')
    result = scan(profile)
    assert result.reset == 1 and len(result.findings) == 2


@pytest.mark.skipif(sys.platform != 'win32', reason='directory junctions are Windows-only')
def test_junction_in_a_layer_path_resets_without_following_it(profile, tmp_path):
    import _winapi
    outside = tmp_path / 'artist' / 'Unreal Engine'
    (outside / 'Engine' / 'Config').mkdir(parents=True)
    precious = outside / 'Engine' / 'Config' / 'UserEngine.ini'
    precious.write_text('[ConsoleVariables]\nr.A=1\n')
    _winapi.CreateJunction(str(outside), str(profile / 'Profile' / 'Documents' / 'Unreal Engine'))
    result = scan(profile)
    assert result.reset == 1 and any('is a link' in f for f in result.findings)
    assert precious.exists()  # the junction was moved as a link, its target untouched
    assert profile_config.config_layers(profile)[2].is_dir()
    assert not os.path.isjunction(profile / 'Profile' / 'Documents' / 'Unreal Engine')


def test_engine_console_variables_startup_count(tmp_path):
    ini = tmp_path / 'UE_5.8' / 'Engine' / 'Config' / 'ConsoleVariables.ini'
    ini.parent.mkdir(parents=True)
    ini.write_text('[Startup]\n; comment\nr.Streaming.PoolSize=100\nr.Other=1\n\n[Other]\nr.Ignored=1\n')
    counted = profile_config.count_engine_cvars(tmp_path / 'UE_5.8', merged_names=['R.STREAMING.POOLSIZE'])
    assert counted.count == 2 and counted.names == ('r.Streaming.PoolSize', 'r.Other')
    assert counted.collisions == ('r.Streaming.PoolSize',)
    assert profile_config.count_engine_cvars(tmp_path / 'absent').count == 0


def _touch(path, age_days, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x')
    stamp = now - age_days * 86400
    os.utime(path, (stamp, stamp))


def test_housekeeping_clears_temp_and_trims_logs_but_not_driver_caches(profile):
    now = time.time()
    _touch(profile / 'Temp' / 'UnrealShaderWorkingDir' / 'job' / 'a.tmp', 0, now)
    _touch(profile / 'Temp' / 'Build.bat.lock', 0, now)
    logs = profile.joinpath(*profile_housekeeping.trimmed_dirs()[0])
    for index in range(25):
        _touch(logs / f'Log-{index:02d}.log', index * 0.1, now)
    _touch(logs / 'Ancient.log', 30, now)
    crash = profile.joinpath(*profile_housekeeping.trimmed_dirs()[1]) / 'UECC-old' / 'report.txt'
    _touch(crash, 10, now)
    os.utime(crash.parent, (now - 10 * 86400, now - 10 * 86400))
    driver = profile / 'Profile' / 'AppData' / 'Local' / 'NVIDIA' / 'DXCache' / 'cache.bin'
    _touch(driver, 60, now)
    result = profile_housekeeping.housekeeping(profile, now=now)
    assert list((profile / 'Temp').iterdir()) == [] and (profile / 'Temp').is_dir()
    remaining = sorted(path.name for path in logs.iterdir())
    assert len(remaining) == 20 and 'Ancient.log' not in remaining and 'Log-00.log' in remaining
    assert not crash.parent.exists()
    assert driver.exists()
    assert result.failed == 0 and result.temp_removed == 2 and result.trimmed == 7


def test_housekeeping_on_a_missing_profile_never_raises(tmp_path):
    result = profile_housekeeping.housekeeping(tmp_path / 'absent')
    assert result.failed == 0
