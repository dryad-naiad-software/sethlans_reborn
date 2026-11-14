# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``clean_env_for_unreal`` (FR-UE-18; AC-43) and the chunk-7 ``[unreal]`` keys (FR-WC-2; AC-41, AC-52)."""
import logging

import pytest

from sethlans_worker_agent import config_unreal, env_cleanup

BASE = {
    'PATH': r'C:\app\_internal;C:\Windows\System32',
    'SystemRoot': r'C:\Windows',
    'PYTHONPATH': 'x', 'PYTHONHOME': 'x', 'PYTHONSTARTUP': 'x', 'PYTHONUSERBASE': 'x', 'PYTHONNOUSERSITE': '1',
    'UE_PYTHONPATH': 'x', 'UE-LocalDataCachePath': 'x', 'ue-shareddatacachepath': 'x',
    'UE-ZenDataPath': 'x', 'ue-zensubprocessdatapath': 'x', 'UE-ZenLimitProcessLifetime': '1',
    '_MEIPASS2': 'x', 'SETHLANS_API_TOKEN': 'secret', 'SETHLANS_UNREAL_SHARE': r'\\nas\s',
}


def test_scrub_removes_every_listed_key_even_when_not_frozen(monkeypatch):
    monkeypatch.setattr(env_cleanup.sys, 'frozen', False, raising=False)
    env = env_cleanup.clean_env_for_unreal(r'D:\uecache\j\42-1a2b3c4d\params.json', base_env=BASE)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE', 'PYTHONNOUSERSITE', 'UE_PYTHONPATH',
                'UE-LocalDataCachePath', 'ue-shareddatacachepath', 'UE-ZenDataPath', 'ue-zensubprocessdatapath',
                '_MEIPASS2', 'SETHLANS_API_TOKEN', 'SETHLANS_UNREAL_SHARE', 'UE-ZenLimitProcessLifetime'):
        assert key not in env
    assert env['SystemRoot'] == r'C:\Windows'
    assert env['PATH'] == r'C:\Windows\System32'
    assert env['SETHLANS_UE_JOB_PARAMS'] == r'D:\uecache\j\42-1a2b3c4d\params.json'


def test_scrub_without_params_sets_no_sethlans_key():
    env = env_cleanup.clean_env_for_unreal(None, base_env=BASE)
    assert not any(key.upper().startswith('SETHLANS_') for key in env)


def test_blender_cleanup_is_unchanged_when_not_frozen(monkeypatch):
    monkeypatch.setattr(env_cleanup.sys, 'frozen', False, raising=False)
    assert env_cleanup.clean_env_for_blender() is None


def _load(**values):
    lowered = {key.lower(): value for key, value in values.items()}
    return config_unreal.load(lambda section, key, default: lowered.get(key, default))


def test_chunk7_defaults():
    values = _load()
    assert values['QUIT_GRACE_SECONDS'] == 60
    assert values['LOG_MAX_MB'] == 512
    assert values['LOG_FLOOD_MB_PER_MIN'] == 100
    assert values['ZEN_PORT'] == 8668
    assert values['HANG_SECONDS'] == 900


@pytest.mark.parametrize('port', ['8558', '1023', '65536', 'abc'])
def test_zen_port_falls_back_with_a_warning(caplog, port):
    with caplog.at_level(logging.WARNING):
        assert _load(ZEN_PORT=port)['ZEN_PORT'] == 8668
    assert any('zen_port' in record.getMessage() for record in caplog.records)


def test_zen_port_accepts_another_free_port():
    assert _load(ZEN_PORT='9100')['ZEN_PORT'] == 9100


@pytest.mark.parametrize('key, bad, default', [('QUIT_GRACE_SECONDS', '5', 60), ('LOG_MAX_MB', '15', 512),
                                               ('LOG_FLOOD_MB_PER_MIN', '9', 100)])
def test_bounds_fall_back(key, bad, default):
    assert _load(**{key: bad})[key] == default


def test_every_ue_prefixed_variable_is_removed():
    keys = ['UE-CmdLineArgs', 'ue-cmdlineargs', 'UE_HORDE_URL', 'UE_CRC_TELEMETRY_URL', 'UE_TELEMETRY_DIR',
            'UE-VirtualizationWorkingDir', 'UE-DumpGPUPath', 'UE_PIPINSTALL_PATH', 'UE_EXTERNAL_PROFILER',
            'UE-ZenSharedDataCacheHost', 'UE-CloudDataCacheHost']
    base = {**{key: '-ExecCmds=quit' for key in keys}, 'UEX': 'kept', 'USE_X': 'kept'}
    env = env_cleanup.clean_env_for_unreal(None, base_env=base)
    assert not any(key in env for key in keys)
    assert env['UEX'] == 'kept' and env['USE_X'] == 'kept'


def test_mixed_case_isolation_variables_never_survive():
    base = {'UserProfile': r'C:\Users\artist', 'temp': r'C:\Temp', 'Tmp': r'C:\Temp', 'localappdata': 'x',
            'AppData': 'x', 'HomeDrive': 'C:', 'HomePath': r'\Users\artist'}
    env = env_cleanup.clean_env_for_unreal(None, base_env=base)
    assert env == {}
    assert env_cleanup.UNREAL_ISOLATION_VARS == ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH',
                                                 'TEMP', 'TMP')
