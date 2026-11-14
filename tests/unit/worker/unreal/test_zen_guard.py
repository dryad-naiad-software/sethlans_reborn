# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Zen/DDC data-path guard (Unreal foundation FR-UE-24, FR-T-5 "Zen/DDC
guard", AC-40).
"""
import pytest

from sethlans_worker_agent.unreal_executor import zen_guard

REFUSED = {
    'zen-autolaunch-datapath': '[Zen.AutoLaunch]\nDataPath=D:\\elsewhere\n',
    'mixed-case-array-datapath': '[zen.autolaunch]\n+datapath=D:\\x\n',
    'zen-datapath': '[Zen]\nDataPath=D:\\x\n',
    'desired-port': '[Zen.AutoLaunch]\nDesiredPort=8558\n',
    'ddc-graph-path': ('[InstalledDerivedDataBackendGraph]\n'
                       'Local=(Type=FileSystem, ReadOnly=false, Path=D:\\ddc)\n'),
    'ddc-graph-env-override': '[DerivedDataBackendGraph]\nShared=(Type=FileSystem, EnvPathOverride=UE-SharedDataCachePath)\n',
    'global-local-ddc': '[/Script/UnrealEd.EditorSettings]\nGlobalLocalDDCPath=(Path="D:/ddc")\n',
    'global-shared-ddc': '[/script/unrealed.editorsettings]\n!GlobalSharedDDCPath=ClearArray\n',
}


def _project(tmp_path, ini_text, name='DefaultEngine.ini', encoding='utf-8', sub=''):
    project = tmp_path / 'w' / 'k-s0' / 'Demo'
    config = project / 'Config' / sub if sub else project / 'Config'
    config.mkdir(parents=True)
    (project / 'Demo.uproject').write_text('{}', encoding='utf-8')
    data = ini_text.encode(encoding) if isinstance(ini_text, str) else ini_text
    (config / name).write_bytes(data)
    return project


@pytest.mark.parametrize('case', sorted(REFUSED))
def test_refused_entries(case, tmp_path):
    result = zen_guard.scan_project_config(_project(tmp_path, REFUSED[case]))
    assert not result.ok and result.summary == 'zen_ddc_guard=FAILED'
    assert result.message.startswith('Project config sets a Zen/DDC data path (Config/DefaultEngine.ini: [')
    assert result.message.endswith("farm renders refuse it because Unreal may delete the worker user's DDC.")


def test_mixed_case_array_key_message(tmp_path):
    result = zen_guard.scan_project_config(_project(tmp_path, REFUSED['mixed-case-array-datapath']))
    assert result.message == ("Project config sets a Zen/DDC data path (Config/DefaultEngine.ini: "
                              "[zen.autolaunch] +datapath); farm renders refuse it because Unreal may delete "
                              "the worker user's DDC.")


def test_nested_ini_and_utf16_bom_are_scanned(tmp_path):
    text = '[Zen.AutoLaunch]\r\nDataPath=D:\\x\r\n'
    project = _project(tmp_path, b'\xff\xfe' + text.encode('utf-16-le'), sub='Windows', name='WindowsEngine.ini')
    result = zen_guard.scan_project_config(project)
    assert [hit.relpath for hit in result.hits] == ['Config/Windows/WindowsEngine.ini']


def test_poc_like_config_passes(tmp_path):
    text = ('\ufeff[/Script/Engine.RendererSettings]\nr.Lumen.HardwareRayTracing=True\n'
            '[SystemSettings]\nr.RayTracing.ResidentGeometryMemoryPoolSizeInMB=2048\n'
            '; [Zen] DataPath=commented out\n'
            '[/Script/UnrealEd.EditorSettings]\nbSomething=True\n')
    result = zen_guard.scan_project_config(_project(tmp_path, text))
    assert result.ok and result.summary == 'zen_ddc_guard=ok' and result.message == ''
    assert result.files == ('Config/DefaultEngine.ini',)


def test_ddc_graph_without_paths_passes(tmp_path):
    result = zen_guard.scan_project_config(_project(tmp_path, '[DerivedDataBackendGraph]\nMinimumDaysToKeepFile=7\n'))
    assert result.ok


def test_undecodable_ini_fails(tmp_path):
    result = zen_guard.scan_project_config(_project(tmp_path, b'[Zen]\n\xff\xfe\xfa bad'))
    assert not result.ok and 'cannot be decoded' in result.message


def test_project_without_config_passes(tmp_path):
    project = tmp_path / 'Demo'
    project.mkdir()
    assert zen_guard.scan_project_config(project).ok


def test_non_ini_files_are_ignored(tmp_path):
    project = _project(tmp_path, '[Zen]\nDataPath=x\n', name='notes.txt')
    assert zen_guard.scan_project_config(project).ok


@pytest.mark.parametrize('text', [
    '[Zen.AutoLaunch]\nExtraArgs=--clean\n',
    '[Zen.AutoLaunch]\nLimitProcessLifetime=false\n',
    '[Zen.Something]\nAnything=1\n',
    '[ZEN]\nSomeKey=value\n',
])
def test_any_key_in_a_zen_section_is_refused(tmp_path, text):
    assert not zen_guard.scan_project_config(_project(tmp_path, text)).ok


def test_zen_lookalike_sections_pass(tmp_path):
    assert zen_guard.scan_project_config(_project(tmp_path, '[Zenith]\nA=1\n[MyZen.AutoLaunch]\nB=2\n')).ok


@pytest.mark.parametrize('separator', ['\x85', '\u2028', '\u2029', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\x00'])
def test_non_ue_line_breaks_and_nul_fail_closed(tmp_path, separator):
    """Sec S2: Python's splitlines() would split here, UE would not; refuse instead of guessing."""
    text = f'[/Script/Engine.Engine]{separator}[Zen.AutoLaunch]\nDataPath=x\n'
    result = zen_guard.scan_project_config(_project(tmp_path, text))
    assert not result.ok and 'non-CR/LF line break' in result.message


def test_lines_split_on_cr_lf_and_crlf_only():
    text = '[A]\rkey=1\r\n[Zen]\nDataPath=x'
    hits = zen_guard.scan_ini_text(text, 'x.ini')
    assert [(h.section, h.key) for h in hits] == [('Zen', 'DataPath')]


@pytest.mark.parametrize('location', ['Saved/Config/WindowsEditor/Engine.ini', 'Plugins/P/Config/DefaultP.ini',
                                      'Platforms/Win/Config/WinEngine.ini', 'Config/Deep/x.ini',
                                      'Content/odd/place.INI'])
def test_ini_files_anywhere_in_the_working_copy_are_scanned(tmp_path, location):
    project = tmp_path / 'Demo'
    path = project.joinpath(*location.split('/'))
    path.parent.mkdir(parents=True)
    path.write_text('[Zen.AutoLaunch]\nDataPath=x\n', encoding='utf-8')
    result = zen_guard.scan_project_config(project)
    assert [hit.relpath for hit in result.hits] == [location]
