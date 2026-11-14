# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Log rules v3 (FR-UE-12, FR-UE-13, FR-UE-15; AC-42)."""
import pytest

from sethlans_worker_agent.unreal_executor import log_validation as lv

P = '[2026.11.01-20.45.12:345][812]'
CANCELLED = P + 'LogComputeFramework: Warning: DG_LatticeDeformer/CacheGeometry: Compilation cancelled.'
MRG_DONE = P + 'LogMovieRenderPipeline: Movie Graph Render completed. Duration: +00:00:57.095'
LEGACY_DONE = P + 'LogMovieRenderPipeline: Movie Pipeline completed. Duration: +00:00:04.643'
EXECUTOR = P + 'LogMovieRenderPipeline: MoviePipelineLinearExecutorBase finished 1 jobs in +00:00:26.180.'
EXIT = P + 'LogWindows: FPlatformMisc::RequestExitWithStatus(0, 0, <NoCallSiteInfo>)'
DDC_MISS = P + 'LogComputeFramework: Loading shader for kernel X from DDC failed. Shader needs recompile.'
REAL_FAIL = P + 'LogComputeFramework: Error: kernel Foo failed to compile'
APPLY = [P + f'LogMovieRenderPipeline: Applying CVar "{name}" with value 2.' for name in (
    'Constraints.SkeletalMesh.TickingMode', 'r.VolumetricFog.HistoryWeight', 'r.Lumen.HardwareRayTracing.LightingMode')]
IGNORED = (P + "LogConsoleManager: Warning: Setting the console variable 'r.Streaming.PoolSize' with "
           "'SetBySystemSettingsIni' was ignored as it is lower priority than the previous 'SetByDeviceProfile'. "
           "Value remains '9000'")
DP_SET = P + 'LogInit: Setting CommandLine Device Profile CVar: [[r.Streaming.PoolSize:9000]]'


def test_version_is_3():
    assert lv.LOG_RULES_VERSION == 3


def test_cancellations_after_exit_are_counted():
    scan = lv.scan_lines([MRG_DONE, EXECUTOR, EXIT, CANCELLED, CANCELLED])
    assert scan.count('compilation_cancelled') == 2
    assert scan.count('exit_status_zero') == 1 and scan.executor_jobs() == 1


def test_legacy_and_graph_completion_durations():
    assert lv.render_time_seconds(lv.scan_lines([LEGACY_DONE]), 99.0) == 5
    assert lv.scan_lines([MRG_DONE]).mrq_duration_s() == pytest.approx(57.095)
    assert lv.render_time_seconds(lv.scan_lines([EXIT]), 77.4) == 77


def test_ddc_cache_miss_is_not_a_compute_error():
    assert lv.scan_lines([DDC_MISS] * 16).count('compute_framework_error') == 0
    assert lv.scan_lines([REAL_FAIL]).count('compute_framework_error') == 1


@pytest.mark.parametrize('line', [
    P + 'LogD3D12RHI: Error: DXGI_ERROR_DEVICE_REMOVED',
    P + 'LogRHI: Error: GPU crashed',
    P + 'LogVulkanRHI: VK_ERROR_DEVICE_LOST',
])
def test_gpu_loss_is_fatal(line):
    scan = lv.scan_lines([line])
    assert scan.fatal_hits() == ['gpu_device_lost']


def test_non_rhi_device_removed_is_not_gpu_loss():
    assert lv.scan_lines([P + 'LogInput: Device removed (gamepad 0)']).count('gpu_device_lost') == 0


def test_critical_error_is_fatal():
    assert lv.scan_lines([P + 'LogWindows: Error: === Critical error: ===']).fatal_hits() == ['critical_error']


def test_cvar_captures_and_priority_ignored_is_info():
    scan = lv.scan_lines(APPLY + [IGNORED, DP_SET])
    assert scan.captured_names('mrq_cvar_applied')[0] == 'Constraints.SkeletalMesh.TickingMode'
    assert scan.captures['cvar_priority_ignored'] == [('r.Streaming.PoolSize', '9000')]
    assert scan.captured_names('dp_cvar_set') == ['r.Streaming.PoolSize']
    assert scan.fatal_hits() == []
    rule = next(r for r in lv.LOG_RULES if r.name == 'cvar_priority_ignored')
    assert rule.severity == 'info'


def test_log_flood_is_a_fatal_size_rule_not_a_line_rule():
    names = {rule.name for rule in lv.fatal_rules()}
    assert names == {'gpu_device_lost', 'critical_error', 'cmdline_env_injection'}
    flood = next(r for r in lv.LOG_RULES if r.name == lv.LOG_FLOOD)
    assert flood.severity == 'fatal' and not flood.is_line_rule


def test_scan_log_reads_whole_file(tmp_path):
    log = tmp_path / 'render.1.log'
    log.write_bytes(('﻿' + '\n'.join([MRG_DONE, EXIT] + ['x'] * 10000 + [CANCELLED]) + '\n').encode('utf-8')
                    + b'\xff\xfe broken bytes\n')
    scan = lv.scan_log(log)
    assert scan.count('compilation_cancelled') == 1 and scan.count('mrq_completed') == 1


def test_unknown_severity_is_rejected():
    with pytest.raises(ValueError):
        lv._rule('x', 'y', 'catastrophic')
