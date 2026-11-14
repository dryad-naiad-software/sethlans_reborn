# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker poll parameters with Unreal (Unreal foundation FR-WD-9, FR-WX-7,
FR-WX-13, NF-6, AC-9, AC-21, AC-33; FR-T-7 ``test_unreal_poll_params.py``).

Real ``job_processor._build_poll_params`` -> ``unreal_bridge`` ->
capabilities -> share state, with a real ``WorkerCapacity``.
"""
import pytest

from sethlans_worker_agent import config_unreal, job_processor
from tests._unreal_worker_state import (  # noqa: F401
    BUILD_58, enable_unreal, reachable, unreal_runtime_reset,
)
from tests.unit.worker.executors._fakes import install_capacity, reset_chunk3_state

pytestmark = pytest.mark.usefixtures('unreal_runtime_reset')

PREFS = ['GPU', 'CPU', 'ANY']
TODAY = {
    'status': 'QUEUED', 'assigned_worker__isnull': 'true',
    'device_prefs': 'GPU,CPU,ANY', 'available_versions': '4.2.1,4.5.0',
}
SHARE = r'\\nas.lan\sethlans'


@pytest.fixture(autouse=True)
def _blenders(mocker):
    reset_chunk3_state()
    mocker.patch(
        'sethlans_worker_agent.job_processor.tool_manager_instance.scan_for_local_blenders',
        return_value=[{'version': '4.2.1'}, {'version': '4.5.0'}],
    )
    saved = job_processor._capacity
    yield
    job_processor._capacity = saved
    reset_chunk3_state()


def _params():
    return job_processor._build_poll_params(PREFS)


def test_disabled_is_byte_identical_to_today(monkeypatch):
    monkeypatch.setattr(config_unreal, 'ENABLED', False)
    install_capacity(gpu_slot_count=2, cpu_slot_count=1)
    params = _params()
    assert params == TODAY and list(params) == list(TODAY)


def test_enabled_idle_and_reachable_asks_for_unreal(monkeypatch):
    enable_unreal(monkeypatch, share=SHARE, share_state=reachable(SHARE))
    install_capacity(gpu_slot_count=1, cpu_slot_count=1)
    assert _params() == dict(TODAY, renderers='BLENDER,UNREAL', unreal_builds=BUILD_58)


def test_enabled_but_busy_is_byte_identical(monkeypatch):
    """Partially busy machine (2 GPUs, one rendering Blender): no UNREAL (D1c)."""
    enable_unreal(monkeypatch, share=SHARE, share_state=reachable(SHARE))
    cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
    cap.reserve_for_job(41, 'GPU')
    assert _params() == TODAY
    cap.release_slot(41)
    assert 'renderers' in _params()


def test_share_unconfigured_is_byte_identical(monkeypatch):
    enable_unreal(monkeypatch, share='')
    install_capacity()
    assert _params() == TODAY


def test_share_unreachable_is_byte_identical(monkeypatch):
    enable_unreal(monkeypatch, share=SHARE, share_state=reachable(SHARE, ok=False))
    install_capacity()
    assert _params() == TODAY


def test_share_check_not_finished_yet_is_byte_identical(monkeypatch):
    """Real share state before the first check result: unreachable (FR-WC-7)."""
    enable_unreal(monkeypatch, share=SHARE)
    install_capacity()
    assert _params() == TODAY


def test_unreal_disabled_for_a_reason_is_byte_identical(monkeypatch):
    enable_unreal(monkeypatch, share=SHARE, share_state=reachable(SHARE), force_gpu_index='0')
    install_capacity()
    assert _params() == TODAY


@pytest.mark.parametrize('enabled', [False, True])
def test_never_sends_ordering(monkeypatch, enabled):
    if enabled:
        enable_unreal(monkeypatch, share=SHARE, share_state=reachable(SHARE))
    else:
        monkeypatch.setattr(config_unreal, 'ENABLED', False)
    install_capacity()
    assert 'ordering' not in _params()
