# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal Engine detection (Unreal foundation FR-WD-1..5, AC-8;
FR-T-5 "Detection").
"""
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from sethlans_worker_agent.unreal_executor import detection

windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='Windows detection (phase 1)')

THETA_UE58 = Path(r'C:\Program Files\Epic Games\UE_5.8')


def build_version(major, minor, patch, changelist):
    return {"MajorVersion": major, "MinorVersion": minor, "PatchVersion": patch,
            "Changelist": changelist, "CompatibleChangelist": changelist, "IsLicenseeVersion": 0,
            "IsPromotedBuild": 1, "BranchName": f"++UE5+Release-{major}.{minor}"}


def make_engine(root: Path, build=None, raw=None):
    (root / 'Engine/Build').mkdir(parents=True)
    (root / 'Engine/Binaries/Win64').mkdir(parents=True)
    (root / 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe').write_bytes(b'MZ')
    text = raw if raw is not None else json.dumps(build_version(*build))
    (root / 'Engine/Build/Build.version').write_text(text, encoding='utf-8-sig')
    return root


def theta_launcher_dat(path: Path, ue58: Path, ue57: Path, extra=()):
    """theta's LauncherInstalled.dat: engines plus plugin/marketplace entries (2026-11-14)."""
    entries = [
        (ue58, 'Blueprin0b23223502eaV17', '5.8.0-59291227+++UE5+Dev-Marketplace-Windows'),
        (ue57, 'FabPlugin_5.7', '5.7.0-47537391+++UE5+Release-5.7-0.0.15-59183337-Windows'),
        (ue58, 'UE_5.8', '5.8.4-58902052+++UE5+Release-5.8-Windows'),
        (ue58, 'FabPlugin_5.8', '5.8.0-55116800+++UE5+Release-5.8-0.0.21-59183337-Windows'),
        (ue57, 'QuixelBridge_5.7', '5.7.0-47537391+++UE5+Release-5.7-2025.0.1-47537391-Windows'),
        (ue57, 'UE_5.7', '5.7.4-51494982+++UE5+Release-5.7-Windows'),
        (ue58, 'QuixelBridge_5.8', '5.8.0-55116800+++UE5+Release-5.8-2025.0.1-55116800-Windows'),
        (ue58, 'NWIROAIIf0b7fbfe049eV4', '5.8.0-59297623+++UE5+Dev-Marketplace-Windows'),
    ] + list(extra)
    data = {"InstallationList": [
        {"InstallLocation": str(loc), "NamespaceId": "", "ItemId": "", "ArtifactId": "",
         "AppVersion": version, "AppName": name} for loc, name, version in entries]}
    path.write_text(json.dumps(data, indent=1), encoding='utf-8-sig')
    return path


@pytest.fixture()
def theta_like(tmp_path, monkeypatch):
    program_files = tmp_path / 'Program Files'
    ue58 = make_engine(program_files / 'Epic Games' / 'UE_5.8', (5, 8, 4, 58902052))
    ue57 = make_engine(program_files / 'Epic Games' / 'UE_5.7', (5, 7, 4, 51494982))
    monkeypatch.setenv('ProgramFiles', str(program_files))
    monkeypatch.setenv('ProgramW6432', str(program_files))
    dat = theta_launcher_dat(tmp_path / 'LauncherInstalled.dat', ue58, ue57)
    return tmp_path, dat, ue58, ue57


@windows_only
class TestThetaFixture:

    def test_two_installs_from_plugin_heavy_launcher_list(self, theta_like):
        _tmp, dat, ue58, ue57 = theta_like
        installs = detection.detect_engines([detection.WindowsLauncherLocator(str(dat))])
        assert [(i.build, i.supported, i.source) for i in installs] == [
            ('5.7.4-51494982', False, 'launcher'), ('5.8.4-58902052', True, 'launcher')]
        assert installs[0].reason == 'UE 5.7 not supported (R14)'
        assert installs[1].editor_cmd == ue58 / 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'

    def test_outside_program_files_is_untrusted_unless_configured(self, theta_like):
        tmp, _dat, _ue58, _ue57 = theta_like
        other = make_engine(tmp / 'Engines' / 'UE_5.8b', (5, 8, 4, 58902052))
        dat = theta_launcher_dat(tmp / 'other.dat', other, other)
        [install] = detection.detect_engines([detection.WindowsLauncherLocator(str(dat))])
        assert install.supported is False and install.reason == detection.UNTRUSTED_LOCATION_REASON
        [install] = detection.detect_engines([detection.ConfiguredPathLocator(str(other)),
                                              detection.WindowsLauncherLocator(str(dat))])
        assert install.supported is True and install.source == 'config'

    @pytest.mark.parametrize('raw,reason', [
        ('{not json', 'unreadable'),
        (json.dumps(build_version(5, 8, 4, 0)), 'Changelist 0'),
        (json.dumps({"MajorVersion": 5, "MinorVersion": "8", "PatchVersion": 4, "Changelist": 1}), 'invalid'),
        (json.dumps({"MajorVersion": True, "MinorVersion": 8, "PatchVersion": 4, "Changelist": 1}), 'invalid'),
        (json.dumps([1, 2]), 'not a JSON object'),
    ])
    def test_malformed_build_version_is_reported_unsupported(self, tmp_path, raw, reason):
        root = make_engine(tmp_path / 'UE_src', raw=raw)
        [install] = detection.detect_engines([detection.ConfiguredPathLocator(str(root))])
        assert install.supported is False and reason in install.reason

    def test_not_an_engine_is_ignored(self, tmp_path):
        (tmp_path / 'Engine/Build').mkdir(parents=True)
        (tmp_path / 'Engine/Build/Build.version').write_text(json.dumps(build_version(5, 8, 4, 1)))
        assert detection.detect_engines([detection.ConfiguredPathLocator(str(tmp_path))]) == []

    def test_dedupes_case_insensitively(self, theta_like):
        _tmp, _dat, ue58, _ue57 = theta_like
        paths = os.pathsep.join([str(ue58), str(ue58).upper(), str(ue58).replace('\\', '/')])
        assert len(detection.detect_engines([detection.ConfiguredPathLocator(paths)])) == 1

    def test_missing_launcher_file(self, tmp_path):
        assert detection.WindowsLauncherLocator(str(tmp_path / 'none.dat')).candidates() == []


class TestNonLocalCandidatesNeverTouchTheFilesystem:

    @pytest.mark.parametrize('candidate', [
        r'\\attacker\share\UE_5.8', '//attacker/share/UE_5.8', r'\\?\C:\UE_5.8',
        r'\\.\C:\UE_5.8', r'relative\UE_5.8', '/opt/UE_5.8', 'C:UE_5.8',
    ])
    def test_rejected_before_stat(self, monkeypatch, candidate):
        def boom(*args, **kwargs):
            raise AssertionError('filesystem touched')

        for name in ('stat', 'lstat'):
            monkeypatch.setattr(os, name, boom)
        monkeypatch.setattr(os.path, 'realpath', boom)
        monkeypatch.setattr(os.path, 'exists', boom)
        monkeypatch.setattr(detection, '_program_files_roots', lambda: [])
        locator = detection.ConfiguredPathLocator(candidate)
        assert detection.detect_engines([locator], system='Windows') == []

    def test_launcher_entry_with_unc_location(self, tmp_path, monkeypatch):
        dat = tmp_path / 'LauncherInstalled.dat'
        dat.write_text(json.dumps({"InstallationList": [{"InstallLocation": r"\\evil\x"}]}))
        candidates = detection.WindowsLauncherLocator(str(dat)).candidates()
        assert candidates == [r'\\evil\x']
        assert not detection.is_local_drive_path(candidates[0])


class TestDriveAndSizeLimits:

    def test_mapped_network_drive_is_refused_before_realpath(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError('filesystem touched')

        monkeypatch.setattr(detection, 'is_fixed_drive', lambda path: False)
        monkeypatch.setattr(os.path, 'realpath', boom)
        monkeypatch.setattr(detection, '_program_files_roots', lambda: [])
        locator = detection.ConfiguredPathLocator(r'V:\applications\UE_5.8')
        assert detection.detect_engines([locator], system='Windows') == []

    def test_config_rejections_warn_once_launcher_ones_stay_debug(self, monkeypatch, caplog):
        caplog.set_level('DEBUG', logger='sethlans_worker_agent.unreal_executor.detection')
        monkeypatch.setattr(detection, '_program_files_roots', lambda: [])
        detection._config_rejections_logged.clear()
        bad = r'\\nas\UE_5.8'
        for _ in range(3):
            detection.detect_engines([detection.ConfiguredPathLocator(bad)], system='Windows')
        warnings = [r for r in caplog.records if r.levelname == 'WARNING']
        assert len(warnings) == 1 and 'SETHLANS_UNREAL_ENGINE_PATHS' in warnings[0].getMessage()

        class Launcher:
            source = 'launcher'

            def candidates(self):
                return [r'\\nas\other']

        caplog.clear()
        detection.detect_engines([Launcher()], system='Windows')
        assert not [r for r in caplog.records if r.levelname == 'WARNING']

    @windows_only
    def test_oversized_launcher_file_is_ignored(self, tmp_path, caplog):
        dat = tmp_path / 'LauncherInstalled.dat'
        dat.write_bytes(b' ' * (detection.MAX_LAUNCHER_DAT_BYTES + 1))
        assert detection.WindowsLauncherLocator(str(dat)).candidates() == []
        assert 'byte limit' in caplog.text

    @windows_only
    def test_system_drive_is_fixed(self):
        assert detection.is_fixed_drive(os.environ.get('SystemDrive', 'C:') + '\\') is True

    def test_not_fixed_off_windows_or_non_drive(self, monkeypatch):
        assert detection.is_fixed_drive(r'\\nas\share') is False


class TestOtherPlatforms:

    def test_returns_empty_and_logs_once(self, caplog):
        caplog.set_level('INFO', logger='sethlans_worker_agent.unreal_executor.detection')
        detection._unsupported_os_logged.discard('Plan9')
        assert detection.detect_engines(system='Plan9') == []
        assert detection.detect_engines(system='Plan9') == []
        assert caplog.text.count('Unreal detection not implemented on Plan9 (phase 4)') == 1


@pytest.mark.skipif(not THETA_UE58.is_dir(), reason='needs a machine with UE 5.8 installed (theta)')
def test_real_machine_detects_ue58():
    """AC-8 on theta (UE 5.8.4 and 5.7.4 installed); elsewhere only 5.8 is required."""
    builds = {i.build: i.supported for i in detection.detect_engines()}
    assert builds.get('5.8.4-58902052') is True
    if socket.gethostname().lower() == 'theta':
        assert builds == {'5.8.4-58902052': True, '5.7.4-51494982': False}
