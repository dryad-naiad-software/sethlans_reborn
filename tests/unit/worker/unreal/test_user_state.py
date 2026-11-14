# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Farm-profile isolation and the known-folder check (FR-UE-27; AC-53)."""
import json
import os
import subprocess
import sys

import pytest

from sethlans_worker_agent.unreal_executor import user_state

PROFILE = r'D:\uecache\ueprofile'
GOOD = {'local_appdata': r'D:\uecache\ueprofile\Profile\AppData\Local',
        'roaming_appdata': r'D:\uecache\ueprofile\Profile\AppData\Roaming',
        'documents': r'D:\uecache\ueprofile\Profile\Documents',
        'temp': 'D:\\uecache\\ueprofile\\Temp\\'}


def probe_line(folders=None, errors=None):
    return json.dumps({'schema': 1, 'folders': GOOD if folders is None else folders, 'errors': errors or {}}) + '\n'


def evaluate(stdout):
    return user_state.evaluate_probe_output(stdout, PROFILE, lambda: 5.0)


def test_isolation_env_values():
    assert user_state.isolation_env(PROFILE) == {
        'USERPROFILE': r'D:\uecache\ueprofile\Profile',
        'LOCALAPPDATA': r'D:\uecache\ueprofile\Profile\AppData\Local',
        'APPDATA': r'D:\uecache\ueprofile\Profile\AppData\Roaming',
        'HOMEDRIVE': 'D:',
        'HOMEPATH': r'\uecache\ueprofile\Profile',
        'TEMP': r'D:\uecache\ueprofile\Temp',
        'TMP': r'D:\uecache\ueprofile\Temp',
    }
    assert user_state.profile_dir(r'D:\uecache') == PROFILE


def test_check_isolated_env_refuses_missing_or_foreign_values():
    env = dict(user_state.isolation_env(PROFILE), PATH='x')
    user_state.check_isolated_env(env, PROFILE)
    for name in user_state.ISOLATION_VARS:
        broken = dict(env)
        del broken[name]
        with pytest.raises(user_state.IsolationError):
            user_state.check_isolated_env(broken, PROFILE)
    with pytest.raises(user_state.IsolationError):
        user_state.check_isolated_env(dict(env, USERPROFILE=r'C:\Users\artist'), PROFILE)


def test_ensure_profile_creates_the_five_folders_and_recreates_a_missing_one(tmp_path):
    profile = tmp_path / 'ueprofile'
    assert user_state.missing_folders(profile)
    created = user_state.ensure_profile(profile)
    assert created == len(user_state.profile_paths(profile))
    for parts in user_state.PROFILE_FOLDERS:
        assert profile.joinpath(*parts).is_dir()
    assert user_state.missing_folders(profile) == ()
    os.rmdir(profile / 'Profile' / 'AppData' / 'LocalLow')
    assert user_state.missing_folders(profile) == ('Profile\\AppData\\LocalLow',)
    assert user_state.ensure_profile(profile) == 1


def test_probe_output_passes_with_trailing_separator_and_case():
    folders = dict(GOOD, documents=GOOD['documents'].upper() if sys.platform == 'win32' else GOOD['documents'])
    result = evaluate(probe_line(folders))
    assert result.ok and result.checked_at == 5.0


def test_probe_output_passes_with_short_names(monkeypatch):
    monkeypatch.setattr(user_state.zen_private.win32_proc, 'long_path_name',
                        lambda p: p.replace('UECACH~1', 'uecache'))
    folders = {k: v.replace('uecache', 'UECACH~1') for k, v in GOOD.items()}
    assert evaluate(probe_line(folders)).ok


@pytest.mark.parametrize('folders, errors, label', [
    (dict(GOOD, documents=r'C:\Users\x\OneDrive\Documents'), {}, 'Documents'),
    (dict(GOOD, local_appdata=r'C:\Users\x\AppData\Local'), {}, 'Local AppData'),
    (dict(GOOD, roaming_appdata=r'\\server\profiles\x'), {}, 'Roaming AppData'),
    (dict(GOOD, temp='C:\\Temp\\'), {}, 'the temp folder'),
    (dict(GOOD, documents=None), {'documents': '0x80070003'}, 'Documents'),
])
def test_probe_output_failures(folders, errors, label):
    result = evaluate(probe_line(folders, errors))
    assert not result.ok and result.folder == label
    assert result.reason.startswith('Windows shell folders are redirected (')
    assert 'Use a dedicated render account.' in result.reason
    if errors:
        assert '0x80070003' in result.reason


@pytest.mark.parametrize('stdout', ['', 'not json', '{"schema": 1}', '[1, 2]', '{"folders": []}'])
def test_unparseable_probe_output_fails(stdout):
    assert not evaluate(stdout).ok


def test_check_known_folders_runs_the_probe_in_the_isolation_env(monkeypatch):
    monkeypatch.setenv('USERPROFILE', r'C:\Users\artist')
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0, stdout=probe_line(), stderr='')
    result = user_state.check_known_folders(PROFILE, run=run, clock=lambda: 1.0)
    assert result.ok
    assert seen['env']['USERPROFILE'] == r'D:\uecache\ueprofile\Profile'
    assert seen['timeout'] == user_state.PROBE_TIMEOUT_SECONDS
    assert seen['argv'][:2] == [sys.executable, '-I']


def test_probe_timeout_and_spawn_failure_fail():
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])
    assert not user_state.check_known_folders(PROFILE, run=timeout).ok
    assert 'timeout' in user_state.check_known_folders(PROFILE, run=timeout).reason
    assert not user_state.check_known_folders(PROFILE, run=missing).ok


def test_frozen_probe_command_uses_the_reserved_argument(monkeypatch):
    monkeypatch.setattr(user_state.sys, 'frozen', True, raising=False)
    assert user_state.probe_command() == [sys.executable, '--sethlans-known-folder-probe']


def test_run_worker_handles_the_probe_argument_before_startup(monkeypatch):
    import importlib.util
    path = os.path.join(os.path.dirname(user_state.__file__), '..', '..', 'run_worker.py')
    spec = importlib.util.spec_from_file_location('run_worker_under_test', os.path.abspath(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.KNOWN_FOLDER_PROBE_ARG == user_state.PROBE_ARG
    calls = []
    monkeypatch.setattr(user_state.known_folder_probe, 'main', lambda: calls.append('probe') or 0)
    with pytest.raises(SystemExit) as exit_info:
        module._run_known_folder_probe_if_requested(['worker.exe', user_state.PROBE_ARG])
    assert exit_info.value.code == 0 and calls == ['probe']
    assert module._run_known_folder_probe_if_requested(['worker.exe']) is None  # no probe argument
    assert calls == ['probe']


@pytest.mark.skipif(sys.platform != 'win32', reason='SHGetKnownFolderPath is Windows-only')
def test_probe_child_prints_one_json_line():
    completed = subprocess.run(user_state.probe_command(), capture_output=True, text=True, timeout=20)
    data = json.loads(completed.stdout)
    assert set(data['folders']) == {'local_appdata', 'roaming_appdata', 'documents', 'temp'}
