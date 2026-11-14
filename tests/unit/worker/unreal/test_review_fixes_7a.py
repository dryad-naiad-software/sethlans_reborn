# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Chunk 7a review fixes: linear log rules, bounded scans, profile repair, DDC keys, engine CVars (S1-S4, D2-D6, L8)."""
import os
import sys
import time

import pytest

from sethlans_worker_agent.unreal_executor import (
    isolation_state, log_validation as lv, prelaunch, profile_config, user_state, zen_guard, zen_log, zen_port)
from sethlans_worker_agent.unreal_executor.log_tail import MB

P = '[2026.11.14-01.02.03:456][  0]'

# The exact EditorSettings.ini UE 5.8.4 wrote into the research's final-recipe farm profile
# (D:\uespike\iso\iso3\Profile\AppData\Local\UnrealEngine\5.8\Saved\Config\WindowsEditor\EditorSettings.ini).
ISO3_EDITOR_SETTINGS = (
    ';METADATA=(Diff=true, UseCommands=true)\n'
    '[/Script/UnrealEd.EditorSettings]\n'
    'LocalDerivedDataCache=(Path="")\n'
    'SharedDerivedDataCache=(Path="")\n'
    'bEnableDDCNotifications=True\n'
    'bNotifyUseUnrealCloudDDC=True\n'
    'bNotifySetupDDCPath=True\n'
    'bNotifyEnableS3DD=True\n'
    'bEnableS3DDC=True\n'
    'RecentlyOpenedProjectFiles=(ProjectName="D:/uespike/wc/aud1/ACOMAnimationSample/ACOMAnimationSample.uproject",'
    'LastOpenTime=2026.11.14-10.21.13)\n'
    'AutoScalabilityWorkScaleAmount=1.000000\n'
    '\n')
ISO3_MANIFEST = '[Manifest]\nVersion=2\n\n'


# --- S3: linear rules, bounded scans --------------------------------------------------------------

ADVERSARIAL = [
    'LogRHI' * (1024 * 1024 // 6),
    'LogComputeFramework: ' * (1024 * 1024 // 21),
    "Setting the console variable '" * (1024 * 1024 // 31),
    'LogMovieRenderPipeline: Applying CVar "' + 'x' * (1024 * 1024),
]


@pytest.mark.parametrize('line', ADVERSARIAL, ids=['rhi', 'compute', 'priority', 'applying'])
def test_a_one_mebibyte_adversarial_line_scans_quickly(line):
    rules = lv.line_rules()
    lv.scan_lines(['warm-up'], rules)
    started = time.perf_counter()
    scan = lv.scan_lines([line], rules)
    assert time.perf_counter() - started < 0.5
    assert scan.fatal_hits() == []


@pytest.mark.parametrize('line', ADVERSARIAL, ids=['rhi', 'compute', 'priority', 'applying'])
def test_rules_stay_linear_without_the_line_cap(line, monkeypatch):
    # The 4096-character cap alone would mask a quadratic rule; lift it so the whole line is searched.
    monkeypatch.setattr(lv, 'MAX_RULE_CHARS', 2 * MB)
    rules = lv.line_rules()
    started = time.perf_counter()
    lv.scan_lines([line], rules)
    assert time.perf_counter() - started < 0.5


def test_rules_still_match_far_apart_parts_within_the_cap():
    line = P + 'LogD3D12RHI: Error: ' + 'x' * 3000 + ' DXGI_ERROR_DEVICE_HUNG'
    assert lv.scan_lines([line]).fatal_hits() == ['gpu_device_lost']
    beyond = P + 'LogD3D12RHI: ' + 'x' * lv.MAX_RULE_CHARS + ' DXGI_ERROR_DEVICE_HUNG'
    assert lv.scan_lines([beyond]).fatal_hits() == []


def test_ue_cmdline_args_injection_is_fatal():
    line = P + 'LogInit: Inserting commandline from UE-CmdLineArgs: -ExecCmds=quit'
    assert lv.scan_lines([line]).fatal_hits() == ['cmdline_env_injection']
    assert 'cmdline_env_injection' in {rule.name for rule in lv.fatal_rules()}


def test_mrq_day_form_and_unique_captures():
    lines = [P + 'LogMovieRenderPipeline: Movie Graph Render completed. Duration: +1.02:03:04.500'] * 3
    scan = lv.scan_lines(lines)
    assert scan.mrq_duration_s() == pytest.approx(86400 + 2 * 3600 + 3 * 60 + 4.5)
    assert len(scan.captures['mrq_completed']) == 1 and scan.count('mrq_completed') == 3


def test_oversized_log_is_a_flood_and_is_not_scanned(tmp_path):
    log = tmp_path / 'render.1.log'
    log.write_bytes(b'x' * (2 * MB))
    scan = lv.scan_log(log, max_bytes=MB)
    assert scan.oversized and scan.fatal_hits() == ['log_flood'] and scan.lines == 0
    assert not zen_log.verify_log(log, r'D:\uecache', 8668, 'render', max_bytes=MB).ok


def test_scan_log_splits_a_huge_line_and_keeps_the_tail(tmp_path):
    log = tmp_path / 'render.1.log'
    cancelled = P + 'LogComputeFramework: Warning: K: Compilation cancelled.'
    log.write_text('y' * (3 * MB) + '\n' + cancelled, encoding='utf-8')  # no trailing newline
    scan = lv.scan_log(log, max_bytes=16 * MB)
    assert scan.count('compilation_cancelled') == 1


def test_zen_log_fails_on_a_zenserver_with_another_data_directory():
    line = P + ('LogZenServiceInstance: Found existing instance running on port 8668 with different data '
                'directory, will attempt shutdown')
    assert not zen_log.verify_lines([line], r'D:\uecache', 8668, require=False).ok


# --- S4: profile repair -----------------------------------------------------------------------------

@pytest.fixture()
def cache(tmp_path):
    path = tmp_path / 'uecache'
    path.mkdir()
    (path / 'trash').mkdir()
    user_state.ensure_profile(path / 'ueprofile')
    return path


@pytest.mark.skipif(sys.platform != 'win32', reason='directory junctions are Windows-only')
@pytest.mark.parametrize('parts', [('Profile', 'AppData', 'Roaming'), ('Temp',)])
def test_a_junction_in_the_profile_is_moved_aside_and_recreated(cache, tmp_path, parts):
    import _winapi
    artist = tmp_path / 'artist'
    artist.mkdir()
    (artist / 'precious.txt').write_text('keep me')
    target = cache.joinpath('ueprofile', *parts)
    os.rmdir(target)
    _winapi.CreateJunction(str(artist), str(target))
    assert user_state.missing_folders(cache / 'ueprofile')  # a link counts as missing
    result = prelaunch.check_before_launch(
        cache, 8668, [], first_launch=False, port_check=lambda *a, **k: zen_port.PortCheck(ok=True))
    assert result.ok and result.profile_repaired == 1
    assert target.is_dir() and not os.path.isjunction(target)
    assert (artist / 'precious.txt').read_text() == 'keep me'
    assert user_state.missing_folders(cache / 'ueprofile') == ()


def test_a_file_where_a_folder_belongs_is_moved_aside(cache):
    temp = cache / 'ueprofile' / 'Temp'
    os.rmdir(temp)
    temp.write_text('not a folder')
    assert user_state.repair_profile(cache / 'ueprofile', cache / 'trash') == 1
    assert temp.is_dir()


def test_a_clean_profile_needs_no_repair(cache):
    assert user_state.repair_profile(cache / 'ueprofile', cache / 'trash') == 0


# --- D2, D4: DDC keys and the any-ini layers -----------------------------------------------------------

def test_iso3_editor_settings_produce_no_reset(cache):
    profile = cache / 'ueprofile'
    saved = profile_config.config_layers(profile)[0]
    (saved / 'WindowsEditor').mkdir(parents=True)
    (saved / 'Windows').mkdir(parents=True)
    (saved / 'WindowsEditor' / 'EditorSettings.ini').write_text(ISO3_EDITOR_SETTINGS, encoding='utf-8')
    (saved / 'WindowsEditor' / 'Manifest.ini').write_text(ISO3_MANIFEST, encoding='utf-8')
    (saved / 'Windows' / 'Manifest.ini').write_text(ISO3_MANIFEST, encoding='utf-8')
    result = profile_config.scan_farm_profile_config(profile, cache / 'trash')
    assert result.reset == 0 and result.findings == []
    assert (saved / 'WindowsEditor' / 'EditorSettings.ini').exists()


@pytest.mark.parametrize('value, refused', [
    ('(Path="")', False), ('(Path=)', False), ('()', False), ('', False), ('( PATH = "" )', False),
    ('ClearArray', True), ('(Path="D:/ddc")', True), ('(Path=D:\\ddc)', True),
    ('(Path="\\\\nas\\ddc")', True), ('(path=" x ")', True),
    # Review N1 decoys: UE's FParse::Value skips the decoy and reads the second Path=.
    ('(XPath="",Path="\\\\host\\share")', True),
    ('(Foo="Path=""",Path="\\\\host\\share")', True),
], ids=lambda value: str(value)[:40])
@pytest.mark.parametrize('key', ['LocalDerivedDataCache', 'SharedDerivedDataCache', '+SharedDerivedDataCache'])
def test_derived_data_cache_keys_allow_only_ue_empty_forms(key, value, refused):
    text = f'[/Script/UnrealEd.EditorSettings]\n{key}={value}\n'
    assert bool(zen_guard.scan_ini_text(text, 'Config/DefaultEditorSettings.ini')) is refused


def test_global_ddc_keys_stay_refused_as_extras():
    assert zen_guard.scan_ini_text('[/Script/UnrealEd.EditorSettings]\nGlobalLocalDDCPath=(Path="")\n', 'x.ini')


def test_a_ddc_path_in_the_saved_config_layer_resets(cache):
    profile = cache / 'ueprofile'
    saved = profile_config.config_layers(profile)[0] / 'WindowsEditor'
    saved.mkdir(parents=True)
    (saved / 'EditorSettings.ini').write_text(
        ISO3_EDITOR_SETTINGS.replace('SharedDerivedDataCache=(Path="")', 'SharedDerivedDataCache=(Path="\\\\nas\\ddc")'))
    assert profile_config.scan_farm_profile_config(profile, cache / 'trash').reset == 1


def test_scalability_groups_do_not_reset(cache):
    profile = cache / 'ueprofile'
    saved = profile_config.config_layers(profile)[0] / 'WindowsEditor'
    saved.mkdir(parents=True)
    (saved / 'GameUserSettings.ini').write_text('[ScalabilityGroups]\nsg.ResolutionQuality=100\n')
    assert profile_config.scan_farm_profile_config(profile, cache / 'trash').reset == 0


@pytest.mark.parametrize('index', profile_config.ANY_INI_LAYERS)
def test_any_ini_in_a_user_layer_resets(cache, index):
    profile = cache / 'ueprofile'
    layer = profile_config.config_layers(profile)[index]
    layer.mkdir(parents=True, exist_ok=True)
    (layer / 'UserEngine.ini').write_text('[Core.Log]\nGlobal=Log\n')
    result = profile_config.scan_farm_profile_config(profile, cache / 'trash')
    assert result.reset == 1 and list(layer.iterdir()) == []


# --- D5: engine ConsoleVariables.ini ------------------------------------------------------------------

def _engine(tmp_path, text):
    ini = tmp_path / 'UE_5.8' / 'Engine' / 'Config' / 'ConsoleVariables.ini'
    ini.parent.mkdir(parents=True)
    ini.write_text(text)
    return tmp_path / 'UE_5.8'


def test_stock_engine_cvars_are_counted_but_do_not_warn(tmp_path):
    root = _engine(tmp_path, '[Startup]\n;r.ShaderDevelopmentMode=1\np.chaos.AllowCreatePhysxBodies=1\n\n'
                             'fx.SkipVectorVMBackendOptimizations=1\n \n')
    counted = profile_config.count_engine_cvars(root)
    assert counted.count == 2 and counted.non_stock == () and not counted.warn


def test_startup_windows_and_non_stock_entries_warn(tmp_path):
    root = _engine(tmp_path, '[Startup]\np.chaos.AllowCreatePhysxBodies=0\n[Startup_Windows]\nr.Streaming.PoolSize=1\n')
    counted = profile_config.count_engine_cvars(root, merged_names=['r.Streaming.PoolSize'])
    assert counted.count == 2 and set(counted.non_stock) == {'p.chaos.AllowCreatePhysxBodies', 'r.Streaming.PoolSize'}
    assert counted.collisions == ('r.Streaming.PoolSize',) and counted.warn


# --- L8 ---------------------------------------------------------------------------------------------

def test_reason_is_not_run_once_a_profile_is_expected():
    isolation_state.reset_for_tests()
    try:
        assert isolation_state.reason() == ''
        isolation_state.expect()
        assert isolation_state.reason() == isolation_state.NOT_RUN_REASON
    finally:
        isolation_state.reset_for_tests()
