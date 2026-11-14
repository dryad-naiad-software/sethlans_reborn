# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Zen log verification (FR-UE-26 "Verification", Dom R3-8; AC-52)."""
import pytest

from sethlans_worker_agent.unreal_executor import zen_log

CACHE = r'D:\uecache'
P = '[2026.11.14-01.02.03:456][  0]'
OVERRIDE = P + r'LogZenServiceInstance: Found command line override ZenDataPath=D:\uecache\zen\data'
LAUNCH = (P + r"LogZenServiceInstance: Display: Launching executable "
          r"'D:\uecache\ueprofile\Profile\AppData\Local\UnrealEngine\Common\Zen\Install\zenserver.exe', "
          r"working dir 'D:\uecache\ueprofile\Profile\AppData\Local\UnrealEngine\Common\Zen\Install', "
          r"data dir 'D:/uecache/zen/data', args '--port 8668 --data-dir D:\uecache\zen\data --http-forceloopback'")
ATTACH = P + ('LogZenServiceInstance: Found existing instance running on port 8668 matching our settings, '
              'no actions needed')
OTHER = P + 'LogInit: Display: Engine is initialized.'


def verify(lines, require=True, port=8668):
    return zen_log.verify_lines(lines, CACHE, port, require=require)


def test_override_and_launch_pass():
    result = verify([OTHER, OVERRIDE, LAUNCH, OTHER])
    assert result.ok and result.override_seen and result.port_seen and result.summary == ''


def test_attach_line_counts_as_the_port():
    assert verify([OVERRIDE, ATTACH]).ok


def test_another_data_path_fails():
    result = verify([P + r'LogZenServiceInstance: Found command line override ZenDataPath=C:\Users\a\Zen\Data', LAUNCH])
    assert not result.ok and result.summary == 'zen=FAILED' and result.message == zen_log.FAILED_MESSAGE


def test_launch_line_with_another_data_dir_fails():
    assert not verify([OVERRIDE, LAUNCH.replace("data dir 'D:/uecache/zen/data'", "data dir 'D:/uecache2/zen/data'")]).ok


def test_launch_line_with_another_port_fails():
    result = verify([OVERRIDE, LAUNCH.replace('--port 8668', '--port 8558')])
    assert not result.ok and '8558' in result.detail


def test_any_zen_line_naming_another_port_fails():
    line = P + "LogZenServiceInstance: Warning: Failed launch service using executable 'z.exe' on port 8558"
    assert not verify([OVERRIDE, LAUNCH, line]).ok


def test_missing_override_line_fails_when_required():
    result = verify([LAUNCH])
    assert not result.ok and 'Found command line override' in result.detail


def test_missing_launch_or_attach_fails_when_required():
    assert not verify([OVERRIDE]).ok


def test_skipped_override_fails():
    line = P + (r'LogZenServiceInstance: Warning: Skipping command line override ZenDataPath=D:\uecache\zen\data '
                'due to an invalid path')
    assert not verify([line, OVERRIDE, LAUNCH]).ok


def test_log_without_zen_lines_passes_only_when_not_required():
    assert verify([OTHER], require=False).ok
    assert not verify([OTHER], require=True).ok


def test_every_phase_requires_the_override_line():
    assert zen_log.REQUIRED_PHASES == {'prep', 'warmup', 'render'}


@pytest.mark.parametrize('phase', ['prep', 'warmup', 'render'])
def test_verify_log_reads_to_eof(tmp_path, phase):
    log = tmp_path / f'{phase}.1.log'
    log.write_text('\n'.join([OTHER] * 1000 + [OVERRIDE, LAUNCH] + [OTHER] * 1000) + '\n', encoding='utf-8')
    assert zen_log.verify_log(log, CACHE, 8668, phase).ok
    log.write_text('\n'.join([OVERRIDE, LAUNCH] + [OTHER] * 5000 + [LAUNCH.replace('8668', '9999')]) + '\n',
                   encoding='utf-8')
    assert not zen_log.verify_log(log, CACHE, 8668, phase).ok


def test_missing_render_log_fails(tmp_path):
    assert not zen_log.verify_log(tmp_path / 'absent.log', CACHE, 8668, 'render').ok
