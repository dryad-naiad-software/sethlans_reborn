# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``run_worker --list-unreal-editors`` (issue #233): the read-only listing
the Windows installer turns into Unreal editor firewall rules.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sethlans_worker_agent import unreal_editor_list as uel
from sethlans_worker_agent.unreal_executor.detection import EngineInstall

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_WORKER = REPO_ROOT / 'worker' / 'run_worker.py'
UE58 = Path(r'C:\Program Files\Epic Games\UE_5.8')
EDITOR = 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'
windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='Unreal detection is Windows-only (phase 1)')


def install(root=UE58, build='5.8.4-58902052', supported=True, editor=None):
    editor_cmd = Path(editor) if editor else root / EDITOR
    return EngineInstall(root=root, editor_cmd=editor_cmd, build=build,
                         supported=supported, reason='', source='launcher')


def run_main(monkeypatch, installs, args=()):
    monkeypatch.setattr(uel, '_detect', lambda: list(installs))
    return uel.main(list(args))


class TestOutputFormat:

    def test_stdout_lists_rule_name_tab_editor_path(self, monkeypatch, capsys):
        assert run_main(monkeypatch, [install()]) == 0
        expected = f"Sethlans Unreal Editor (5.8.4-58902052)\t{UE58 / EDITOR}\n"
        assert capsys.readouterr().out == expected

    def test_output_file_is_crlf_and_atomic(self, monkeypatch, tmp_path):
        other = Path(r'D:\Program Files\UE_5.8')
        out = tmp_path / 'unreal_firewall_rules.txt'
        assert run_main(monkeypatch, [install(), install(other)], [uel.OUTPUT_OPT, str(out)]) == 0
        lines = out.read_bytes().split(b'\r\n')
        assert lines[-1] == b'' and len(lines) == 3  # two CRLF-terminated lines
        assert lines[0].split(b'\t') == [b'Sethlans Unreal Editor (5.8.4-58902052)', str(UE58 / EDITOR).encode()]
        assert lines[1].startswith(b'Sethlans Unreal Editor (5.8.4-58902052) #2\t')
        assert [p.name for p in tmp_path.iterdir()] == [out.name]  # no temp file left behind

    def test_rule_names_are_unique_per_build(self):
        rules = uel.editor_rules([install(Path(r'C:\A')), install(Path(r'C:\B'), build='5.8.5-1'),
                                  install(Path(r'C:\C'))])
        assert [name for name, _path in rules] == [
            'Sethlans Unreal Editor (5.8.4-58902052)', 'Sethlans Unreal Editor (5.8.5-1)',
            'Sethlans Unreal Editor (5.8.4-58902052) #2']

    def test_existing_output_file_is_replaced(self, monkeypatch, tmp_path):
        out = tmp_path / 'rules.txt'
        out.write_bytes(b'stale\r\n')
        assert run_main(monkeypatch, [], [uel.OUTPUT_OPT, str(out)]) == 0
        assert out.read_bytes() == b''


class TestExclusions:

    @pytest.mark.parametrize('kwargs', [
        {'supported': False},
        {'build': '5.7.4-51494982'},
        {'build': None, 'supported': False},
        {'build': '5.8.4-0'},
    ], ids=['unsupported', 'other-series', 'no-build', 'source-build'])
    def test_unsupported_engines_are_left_out(self, kwargs):
        assert uel.editor_rules([install(**kwargs)]) == []

    @pytest.mark.parametrize('editor', [
        r'\\attacker\share\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe',
        r'\\?\UNC\attacker\share\UnrealEditor-Cmd.exe',
        r'Engine\Binaries\Win64\UnrealEditor-Cmd.exe',
        '/opt/UE_5.8/Engine/Binaries/Linux/UnrealEditor-Cmd',
        'C:\\Program Files\\UE"\\UnrealEditor-Cmd.exe',
        'C:\\Program Files\\UE\t5.8\\UnrealEditor-Cmd.exe',
    ], ids=['unc', 'device-unc', 'relative', 'posix', 'quote', 'tab'])
    def test_unc_and_invalid_paths_are_left_out(self, editor):
        assert uel.editor_rules([install(editor=editor)]) == []

    def test_installer_file_drops_overlong_lines(self, monkeypatch, tmp_path):
        long_root = Path('C:\\' + '\\'.join(['d' * 50] * 14))
        out = tmp_path / 'rules.txt'
        assert run_main(monkeypatch, [install(long_root), install()], [uel.OUTPUT_OPT, str(out)]) == 0
        assert out.read_bytes().count(b'\r\n') == 1
        assert all(len(line) <= uel.MAX_LINE_BYTES for line in out.read_bytes().split(b'\r\n'))

    def test_installer_file_drops_unencodable_lines(self, monkeypatch, tmp_path):
        monkeypatch.setattr(uel, '_file_encoding', lambda: 'ascii')
        out = tmp_path / 'rules.txt'
        rules = [install(Path('C:\\Engines\\\u6f22\u5b57')), install()]
        assert run_main(monkeypatch, rules, [uel.OUTPUT_OPT, str(out)]) == 0
        assert out.read_bytes().count(b'\r\n') == 1


class TestFailureModes:

    def test_no_launcher_file_prints_nothing_and_exits_0(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv('PROGRAMDATA', str(tmp_path))
        assert uel.main([]) == 0
        assert capsys.readouterr().out == ''
        out = tmp_path / 'rules.txt'
        assert uel.main([uel.OUTPUT_OPT, str(out)]) == 0
        assert out.read_bytes() == b''

    def test_detection_error_yields_no_lines_and_exits_0(self, monkeypatch, capsys):
        def boom():
            raise RuntimeError('bad launcher data')
        monkeypatch.setattr(uel, '_detect', boom)
        assert uel.main([]) == 0
        captured = capsys.readouterr()
        assert captured.out == '' and 'bad launcher data' in captured.err

    @pytest.mark.parametrize('args', [['--bogus'], [uel.OUTPUT_OPT], [uel.OUTPUT_OPT, ''],
                                      [uel.OUTPUT_OPT, 'a', 'b']])
    def test_bad_arguments_exit_2(self, monkeypatch, args, capsys):
        assert run_main(monkeypatch, [install()], args) == 2
        assert uel.USAGE in capsys.readouterr().err

    def test_unwritable_output_exits_1(self, monkeypatch, tmp_path):
        out = tmp_path / 'missing-dir' / 'rules.txt'
        assert run_main(monkeypatch, [install()], [uel.OUTPUT_OPT, str(out)]) == 1
        assert not out.exists()

    def test_windowed_exe_without_stdout_still_exits_0(self, monkeypatch):
        monkeypatch.setattr(sys, 'stdout', None)
        monkeypatch.setattr(sys, 'stderr', None)
        assert run_main(monkeypatch, [install()]) == 0


def _engine(root: Path, major, minor, patch, changelist) -> Path:
    (root / 'Engine/Build').mkdir(parents=True)
    (root / 'Engine/Binaries/Win64').mkdir(parents=True)
    (root / EDITOR).write_bytes(b'MZ')
    build = {'MajorVersion': major, 'MinorVersion': minor, 'PatchVersion': patch, 'Changelist': changelist}
    (root / 'Engine/Build/Build.version').write_text(json.dumps(build), encoding='utf-8')
    return root


@windows_only
def test_real_detection_lists_only_supported_launcher_engines(monkeypatch, tmp_path, capsys):
    program_files = tmp_path / 'Program Files'
    ue58 = _engine(program_files / 'Epic Games' / 'UE_5.8', 5, 8, 4, 58902052)
    ue57 = _engine(program_files / 'Epic Games' / 'UE_5.7', 5, 7, 4, 51494982)
    outside = _engine(tmp_path / 'Engines' / 'UE_5.8b', 5, 8, 4, 58902052)
    monkeypatch.setenv('ProgramFiles', str(program_files))
    monkeypatch.setenv('ProgramW6432', str(program_files))
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path / 'ProgramData'))
    monkeypatch.setenv('SETHLANS_UNREAL_ENGINE_PATHS', str(outside))  # not read: launcher only
    locations = [ue58, ue57, outside, r'\\attacker\share\UE_5.8', 'relative\\UE_5.8', ue58]
    dat = tmp_path / 'ProgramData' / 'Epic' / 'UnrealEngineLauncher' / 'LauncherInstalled.dat'
    dat.parent.mkdir(parents=True)
    dat.write_text(json.dumps({'InstallationList': [{'InstallLocation': str(loc), 'AppName': 'UE'}
                                                    for loc in locations]}), encoding='utf-8')
    assert uel.main([]) == 0
    expected = f"Sethlans Unreal Editor (5.8.4-58902052)\t{os.path.realpath(ue58 / EDITOR)}\n"
    assert capsys.readouterr().out == expected


def test_run_worker_dispatches_before_agent_startup(monkeypatch):
    spec = importlib.util.spec_from_file_location('run_worker_list_under_test', RUN_WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LIST_UNREAL_EDITORS_ARG == uel.LIST_ARG
    calls = []
    monkeypatch.setattr(uel, 'main', lambda argv: calls.append(argv) or 0)
    with pytest.raises(SystemExit) as exit_info:
        module._run_list_unreal_editors_if_requested(['worker.exe', uel.LIST_ARG, uel.OUTPUT_OPT, 'f'])
    assert exit_info.value.code == 0 and calls == [[uel.OUTPUT_OPT, 'f']]
    assert module._run_list_unreal_editors_if_requested(['worker.exe']) is None
    assert module._run_list_unreal_editors_if_requested(['worker.exe', '--other']) is None


_CHILD = """
import runpy, sys, types
sys.argv = [sys.argv[1], '--list-unreal-editors']
# If the subcommand ever fell through to the agent, fail instead of starting it.
stub = types.ModuleType('sethlans_worker_agent.agent')
stub.main = lambda: sys.exit(99)
sys.modules['sethlans_worker_agent.agent'] = stub
code = 98
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit as exc:
    code = exc.code
loaded = (m for m in sys.modules if m.startswith('sethlans_worker_agent') and m != stub.__name__)
sys.stderr.write('MODULES ' + ' '.join(sorted(loaded)))
sys.exit(code)
"""


def test_subcommand_never_imports_the_agent_or_config(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith('SETHLANS_')}
    env['PROGRAMDATA'] = str(tmp_path)
    completed = subprocess.run([sys.executable, '-c', _CHILD, str(RUN_WORKER)], capture_output=True,
                               text=True, timeout=60, env=env, cwd=str(tmp_path))
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ''
    modules = set(completed.stderr.rsplit('MODULES ', 1)[1].split())
    assert modules <= {'sethlans_worker_agent', 'sethlans_worker_agent.unreal_editor_list',
                       'sethlans_worker_agent.unreal_executor',
                       'sethlans_worker_agent.unreal_executor.detection'}, modules
    assert list(tmp_path.iterdir()) == []  # wrote nothing
