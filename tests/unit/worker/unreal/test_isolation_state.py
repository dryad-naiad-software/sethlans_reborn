# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Known-folder node condition (FR-UE-27, FR-UE-1 check 6b; AC-53) and its runtime/capabilities wiring."""
import logging

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import capabilities, isolation_state, user_state
from tests._unreal_worker_state import enable_unreal, reachable, unreal_runtime_reset  # noqa: F401

FAIL = user_state.KnownFolderResult(
    ok=False, folder='Documents', path=r'C:\Users\x\OneDrive\Documents',
    reason=user_state.REDIRECTED_REASON.format(folder='Documents', path=r'C:\Users\x\OneDrive\Documents'))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def state(monkeypatch):
    isolation_state.reset_for_tests()
    clock = Clock()
    results = []
    calls = []

    def check(profile, clock=None):
        calls.append(profile)
        result = results.pop(0) if results else user_state.KnownFolderResult(ok=True)
        return user_state.KnownFolderResult(result.ok, result.reason, result.folder, result.path, clock())
    monkeypatch.setattr(isolation_state, '_clock', clock)
    monkeypatch.setattr(isolation_state, '_check', check)
    yield clock, results, calls
    isolation_state.reset_for_tests()


def test_nothing_runs_before_registration(state):
    _clock, _results, calls = state
    assert isolation_state.refresh() is None and isolation_state.check_now() is None
    assert calls == [] and isolation_state.reason() == ''
    assert not isolation_state.passed_within(600)


def test_failed_check_is_a_node_condition_until_a_check_passes(state, caplog):
    clock, results, calls = state
    results.append(FAIL)
    with caplog.at_level(logging.WARNING):
        isolation_state.register(r'D:\uecache\ueprofile')
    assert isolation_state.reason() == FAIL.reason
    assert not isolation_state.passed_within(600)
    assert sum('shell folders are redirected' in r.getMessage() for r in caplog.records) == 1
    clock.now += config_unreal.RESCAN_SECONDS
    assert isolation_state.refresh().ok
    assert isolation_state.reason() == '' and isolation_state.passed_within(600)
    assert len(calls) == 2


def test_warning_is_rate_limited_to_once_per_ten_minutes(state, caplog):
    clock, results, _calls = state
    results.extend([FAIL, FAIL, FAIL])
    with caplog.at_level(logging.WARNING):
        isolation_state.register(r'D:\uecache\ueprofile')
        clock.now += 60
        isolation_state.check_now()
        clock.now += isolation_state.WARN_INTERVAL_SECONDS
        isolation_state.check_now()
    assert sum('shell folders are redirected' in r.getMessage() for r in caplog.records) == 2


def test_refresh_runs_only_when_due(state):
    clock, _results, calls = state
    isolation_state.register(r'D:\uecache\ueprofile')
    clock.now += 10
    isolation_state.refresh()
    assert len(calls) == 1
    isolation_state.refresh(force=True)
    assert len(calls) == 2


def test_passed_within_is_check_6b(state):
    clock, _results, _calls = state
    isolation_state.register(r'D:\uecache\ueprofile')
    clock.now += config_unreal.RESCAN_SECONDS + 1
    assert not isolation_state.passed_within(config_unreal.RESCAN_SECONDS)
    assert isolation_state.check_now().ok and isolation_state.passed_within(config_unreal.RESCAN_SECONDS)


def test_a_raising_check_fails_closed(state, monkeypatch):
    def boom(profile, clock=None):
        raise RuntimeError('ctypes exploded')
    monkeypatch.setattr(isolation_state, '_check', boom)
    result = isolation_state.register(r'D:\uecache\ueprofile')
    assert not result.ok and 'shell folders are redirected' in result.reason


def test_capabilities_report_the_condition(state, monkeypatch, unreal_runtime_reset):  # noqa: F811
    _clock, results, _calls = state
    enable_unreal(monkeypatch, share_state=reachable())
    assert capabilities.snapshot()['enabled'] is True
    results.append(FAIL)
    isolation_state.register(r'D:\uecache\ueprofile')
    report = capabilities.snapshot()
    assert report['enabled'] is False and report['disabled_reason'].startswith('Windows shell folders are')
    assert capabilities.poll_params(node_idle=True) == {}


def test_disabled_by_config_runs_no_probe(state, monkeypatch, unreal_runtime_reset):  # noqa: F811
    _clock, _results, calls = state
    monkeypatch.setattr(config_unreal, 'ENABLED', False)
    capabilities.snapshot()
    capabilities.poll_params(node_idle=True)
    assert calls == []


def test_rescan_keeps_check_6b_fresh_across_off_phase_probes(state, monkeypatch, unreal_runtime_reset):  # noqa: F811
    """Conc R2-M1: register off-phase, check_now mid-cycle; 6b must hold for 3 x RESCAN_SECONDS."""
    from sethlans_worker_agent.unreal_executor import runtime
    clock, _results, calls = state
    heartbeat = 30
    monkeypatch.setattr(runtime, '_clock', clock)
    monkeypatch.setattr(runtime.detection, 'detect_engines', lambda **kwargs: [])
    monkeypatch.setattr(runtime.config_unreal.config, 'HEARTBEAT_INTERVAL_SECONDS', heartbeat, raising=False)

    def slow_check(profile, clock=None):
        state[0].now += user_state.PROBE_TIMEOUT_SECONDS  # worst-case probe duration
        calls.append(profile)
        return user_state.KnownFolderResult(ok=True, checked_at=state[0].now)
    monkeypatch.setattr(isolation_state, '_check', slow_check)
    start = clock.now
    runtime.rescan(force=True)                     # the engine-scan phase starts here
    clock.now = start + 100
    isolation_state.register(r'D:\uecache\ueprofile')  # off-phase
    max_age = isolation_state.check_6b_max_age_s()
    rescan_s = config_unreal.RESCAN_SECONDS
    while clock.now < start + 100 + 3 * rescan_s:
        clock.now += heartbeat
        if abs(clock.now - (start + 100 + rescan_s * 1.5)) < heartbeat / 2:
            isolation_state.check_now()                 # a job's first launch, mid-cycle
        runtime.rescan()
        assert isolation_state.passed_within(max_age), f"stale at t={clock.now - start:.0f}"
    assert max_age >= rescan_s + heartbeat + user_state.PROBE_TIMEOUT_SECONDS


def test_check_6b_max_age_covers_a_long_heartbeat(monkeypatch):
    monkeypatch.setattr(isolation_state.config_unreal, 'RESCAN_SECONDS', 600)
    from sethlans_worker_agent import config
    monkeypatch.setattr(config, 'HEARTBEAT_INTERVAL_SECONDS', 300)
    assert isolation_state.check_6b_max_age_s() == 600 + 300 + user_state.PROBE_TIMEOUT_SECONDS
    monkeypatch.setattr(config, 'HEARTBEAT_INTERVAL_SECONDS', 5)
    assert isolation_state.check_6b_max_age_s() == 600 + isolation_state.PASSED_WITHIN_MARGIN_S
