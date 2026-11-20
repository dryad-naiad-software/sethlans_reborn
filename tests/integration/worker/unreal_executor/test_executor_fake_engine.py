# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
UnrealExecutor end to end against a fake ``UnrealEditor-Cmd.exe`` (Unreal
foundation FR-UE-2, FR-UE-8..16, FR-UE-25, FR-UE-26; chunk 7 integration
requirements 2-4, 8-12). Real cache, real working copy, real Job Objects,
real command lines and log checks; the share, GPU and manager are fakes.
"""
import sys
import threading
import time

import psutil
import pytest

from tests.integration.worker.unreal_executor._harness import Harness, summary

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Job Objects, Windows command lines')


@pytest.fixture(autouse=True)
def _forget_refused_projects():
    from sethlans_worker_agent.unreal_executor import adapter_log
    adapter_log.reset_for_tests()
    yield
    adapter_log.reset_for_tests()


def test_normal_job_renders_uploads_and_reports_summary(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    result = h.execute(h.job(42))
    assert result.success and not result.released, result.error_message
    assert result.output_path is None and result.render_time_seconds == 5
    (job_id, name, data), = h.manager.uploads
    assert (job_id, name) == (42, 'sethlans_42.1000.png') and data.startswith(b'\x89PNG')
    s = summary(result)
    assert s['cache'] == 'miss' and s['warmup'] == 'ran' and s['kind'] == 'render'
    assert s['zen'] == f'private:{h.port}' and s['zen_ddc_guard'] == 'ok' and s['job_object'] == 'ok'
    assert s['pristine_integrity'] == 'ok' and s['survivors'] == '' and s['user_state'] == 'isolated'
    assert s['cvars_applied'] == '3/3' and s['compilation_cancelled'] == '0' and s['vram_gate'] == 'measured'
    assert s['user_cvars'] == '0' and s['cvar_overridden'] == '' and s['quit_forced'] == ''
    jobs = list(h.cache.layout.j.iterdir())
    assert len(jobs) == 1 and not (jobs[0] / 'output').exists()  # FR-PA-17 after the report
    assert not list(h.cache.layout.w.iterdir())                    # working copy removed by finish()


def test_second_job_hits_the_cache_and_skips_the_warm_up(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    assert h.execute(h.job(1)).success
    second = h.execute(h.job(2))
    assert second.success, second.error_message
    s = summary(second)
    assert s['cache'] == 'hit' and s['warmup'] == 'skipped'


def test_cancelled_compilations_trigger_one_warm_up_retry(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, opts={'cancel_first_render': True})
    result = h.execute(h.job(7))
    assert result.success, result.error_message
    s = summary(result)
    assert s['warmup'] == 'ran(retry)' and s['compilation_cancelled'] == '0'
    logs = {p.name for p in next(h.cache.layout.j.iterdir()).joinpath('logs').iterdir()}
    assert {'render.1.log', 'render.2.log', 'warmup.1.log', 'warmup.2.log'} <= logs


def test_prep_error_fails_the_job_with_the_sanitized_message(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, opts={'prep_error': 'Preset does not output PNG; phase 1 does not add outputs.'})
    result = h.execute(h.job(9))
    assert not result.success and not result.released
    assert result.error_message == 'Preset does not output PNG; phase 1 does not add outputs.'
    assert not h.manager.uploads


def test_wrong_zen_data_path_fails_with_zen_failed(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, opts={'zen_path': 'C:\\Users\\artist\\AppData\\Local\\UnrealEngine\\Zen'})
    result = h.execute(h.job(11))
    assert not result.success
    assert result.error_message == "Unreal did not use the farm-private Zen data path; refusing to continue."
    assert summary(result)['zen'] == 'FAILED'


def test_shutdown_mid_render_terminates_and_releases(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, opts={'render_sleep': 60})
    shutdown = threading.Event()
    timer = threading.Timer(4.0, shutdown.set)
    timer.start()
    started = time.monotonic()
    try:
        result = h.execute(h.job(13), shutdown=shutdown)
    finally:
        timer.cancel()
    assert result.released and h.manager.releases == [(13, 'shutdown')]
    assert time.monotonic() - started < 30 and not h.manager.uploads
    assert not any((p.info.get('exe') or '').lower() == str(h.exe).lower()
                   for p in psutil.process_iter(['exe']))


def test_warm_up_and_render_run_on_the_chosen_card(tmp_path, monkeypatch):
    """Card selection FR-CU-1/2: a fresh choice per launch; the fake UE exits 3 without one -dx12/-graphicsadapter."""
    h = Harness(tmp_path, monkeypatch)
    result = h.execute(h.job(21))
    assert result.success, result.error_message
    assert h.choices == [(21, None), (21, 'pci:0000:01:00.0')]   # warm-up chooses; render keeps that card (D8)
    logs = next(h.cache.layout.j.iterdir()) / 'logs'
    for name in ('warmup.1.log', 'render.1.log'):
        text = (logs / name).read_text(encoding='utf-8')
        assert 'Found D3D12 adapter 2: Microsoft Basic Render Driver' in text
        assert 'Chosen D3D12 Adapter Id = 1' in text


def _no_fake_ue_left(h):
    return not any((p.info.get('exe') or '').lower() == str(h.exe).lower() for p in psutil.process_iter(['exe']))


@pytest.mark.parametrize('phase, job_id', [('warmup', 23), ('render', 24)])
def test_another_adapter_stops_the_launch_alerts_and_refuses_the_project(tmp_path, monkeypatch, phase, job_id):
    """FR-CU-3 / AC-13 end to end: the line stops the run; the alert, the release and the project refusal follow.

    One project is not enough to disable Unreal: its ini could have forged the line (security review).
    """
    from sethlans_worker_agent.cards import store
    from sethlans_worker_agent.unreal_executor import adapter_log, runtime
    alerts, disabled = [], []
    monkeypatch.setattr(store, 'add_alert', lambda *args: alerts.append(args) or True)
    monkeypatch.setattr(runtime, 'disable_until_restart', disabled.append)
    h = Harness(tmp_path, monkeypatch, opts={'chosen_adapter': 0, 'chosen_phase': phase, 'after_chosen_sleep': 60})
    started = time.monotonic()
    result = h.execute(h.job(job_id))
    assert result.released and h.manager.releases == [(job_id, 'local_conflict')] and not h.manager.uploads
    assert alerts == [('adapter_mismatch', 'pci:0000:01:00.0', 'NVIDIA GeForce RTX 5090',
                       f'Unreal chose adapter 0 instead of 1; project {h.sha[:12]}+none')]
    assert disabled == [] and adapter_log.refused(f'{h.sha}:')
    logs = {p.name for p in next(h.cache.layout.j.iterdir()).joinpath('logs').iterdir()}
    assert ('render.1.log' in logs) is (phase == 'render')
    assert time.monotonic() - started < 40 and _no_fake_ue_left(h)


def test_card_stop_mid_render_releases_and_keeps_the_cache_entry(tmp_path, monkeypatch):
    """C-24 m: the job's own stop ends the render like a shutdown, but finish() runs and the shutdown is not set."""
    from sethlans_worker_agent import blender_stop
    h = Harness(tmp_path, monkeypatch, opts={'render_sleep': 60})
    job = h.job(25)
    blender_stop.register_card_stop(job)
    shutdown = threading.Event()
    timer = threading.Timer(4.0, blender_stop.request_card_stop, (25,))
    timer.start()
    try:
        result = h.execute(job, shutdown=shutdown)
    finally:
        timer.cancel()
    assert result.released and h.manager.releases == [(25, 'worker_hardware_changed')] and not shutdown.is_set()
    assert summary(result)['pristine_integrity'] == 'ok' and _no_fake_ue_left(h)


def test_pilot_measures_every_phase_and_reports_pilot_result(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, opts={'render_sleep': 4, 'phase_sleep': 4})
    result = h.execute(h.job(77, kind='pilot'))
    assert result.success and result.released, result.error_message
    body, = h.manager.pilot_bodies
    assert body['frames'] == 3 and body['warmup_ran'] is True and body['peak_source'] == 'card'
    assert body['card_delta_peak_mb'] == 2000 and body['peak_mb'] == 2000 and body['baseline_used_mb'] == 1000
    assert body['gpu'] == {'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607,
                           'card_key': 'pci:0000:01:00.0'}
    assert body['csv_local_used_peak_mb'] is None and body['engine_build'] == '5.8.4-58902052'
    s = summary(result)
    assert s['pilot_frames'] == '3' and s['warmup'] == 'ran' and s['vram_gate'] == 'pilot'
    assert s['pilot_adapter'] == 'NVIDIA GeForce RTX 5090' and not h.manager.uploads
