# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
AC-8: a ticked card that leaves the OS list while jobs render (card selection chunk 4).

Two GPU jobs run on real job threads, one on card A and one on card C. Card C
then disappears from the OS probe and from Blender's detection. The real
agent-loop entry (``job_processor.maybe_assert_gpu_list_unchanged``) must:
log one CRITICAL line and save one ``card_lost`` alert; set the per-job stop
of the job on card C only (the worker's shutdown event stays clear); keep
the old slots while that job still holds card C; and swap in the new
profile (card A only) once it handed the job back and released its slot,
while the job on card A keeps rendering.

Faked: the saved file (a dict), the OS probe, the Blender lists, the manager
API and the render itself (a stub that runs until its stop or its finish).
"""

import copy
import logging
import threading

import pytest

from sethlans_worker_agent import agent_shutdown, api_handler, blender_executor, blender_hand_back, blender_stop
from sethlans_worker_agent import config, config_compute, config_store, job_processor, job_selection
from sethlans_worker_agent.capacity import card_check, rebuild
from sethlans_worker_agent.cards import inventory, os_probe, startup_log
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.tool_manager import tool_manager_instance

from ._card_choice_harness import _CARDS, BLENDER_LIST, KEY_A, KEY_C, VENDOR, blender_job, saved_record

WAIT = 10.0  # bound for every wait and join; never reached when the code is right
STOPPED = (False, False, False, '', '', blender_stop.STOPPED_MESSAGE, None, None, None, None)
DONE = (True, False, False, '', '', '', None, None, None, None)


class Renders:
    """Stands in for ``blender_executor.execute_blender_job``: runs until its stop or its finish."""

    def __init__(self, job_ids):
        self.started = {job_id: threading.Event() for job_id in job_ids}
        self.stopped = {job_id: threading.Event() for job_id in job_ids}
        self.finish = {job_id: threading.Event() for job_id in job_ids}
        self.hand_back_gate = {job_id: threading.Event() for job_id in job_ids}
        self.cards = {}

    def __call__(self, job_data, reservation=None, shutdown_event=None, **_kwargs):
        job_id = job_data['id']
        stop = blender_stop.job_stop(shutdown_event, job_data)
        self.cards[job_id] = reservation.card_keys
        self.started[job_id].set()
        while not stop.is_set():
            if self.finish[job_id].wait(0.01):
                return DONE
        job_data[blender_stop.STOPPED_KEY] = blender_stop.stop_reason(stop)
        self.stopped[job_id].set()
        assert self.hand_back_gate[job_id].wait(WAIT)  # the test looks at the slots before this job lets go
        return STOPPED


@pytest.fixture
def worker(mocker):
    present = {key for key, _name, _device in _CARDS}
    state = {'compute': {'method': 'gpu', 'gpu_mode': 'split', 'revision': 1,
                         'cards': [saved_record(KEY_A), saved_record(KEY_C)],
                         'known': [key for key, _n, _d in _CARDS]}}

    def set_many(pairs, expect=None):
        for dotted, value in pairs:
            state['compute'][dotted.split('.')[1]] = copy.deepcopy(value)

    def blender_list():
        return [dict(r) for r in BLENDER_LIST if f"pci:{r['id'].rsplit('_', 1)[1]}.0" in present]

    mocker.patch.object(config_compute, '_read_raw', side_effect=lambda: copy.deepcopy(state['compute']))
    mocker.patch.object(config_store, 'set_many', side_effect=set_many)
    mocker.patch.object(os_probe, 'probe', side_effect=lambda: tuple(
        OsCard(key=key, name=name, vendor_id=VENDOR, device_id=device) for key, name, device in _CARDS
        if key in present))
    mocker.patch.object(startup_log, 'cached_blender_list', return_value=([dict(r) for r in BLENDER_LIST], '4.5.1'))
    real_build = inventory.build
    mocker.patch.object(inventory, 'build', side_effect=lambda os_list, blender, version=None, **_kw:
                        real_build(os_list, blender, version, platform_name='linux'))
    mocker.patch.object(card_check, 'detect_physical_gpus_now',
                        side_effect=lambda: [(r['id'], r['type']) for r in blender_list()])
    mocker.patch.object(job_processor.system_monitor, 'get_gpu_device_details', return_value=list(BLENDER_LIST))
    mocker.patch.object(job_processor.system_monitor, 'get_cpu_thread_count', return_value=8)
    mocker.patch.object(config, 'CPU_THREADS', 0)
    mocker.patch.object(config, 'IDLE_DETECTION_ENABLED', False)
    mocker.patch.object(config, 'HEARTBEAT_INTERVAL_SECONDS', 30)
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[])
    renders = Renders((1, 2))
    mocker.patch.object(blender_executor, 'execute_blender_job', side_effect=renders)
    api = {
        'poll': mocker.patch.object(api_handler, 'poll_for_available_jobs', return_value=[]),
        'claim': mocker.patch.object(api_handler, 'claim_job', return_value=True),
        'update': mocker.patch.object(api_handler, 'update_job_status', return_value=None),
        'hand_back': mocker.patch.object(blender_hand_back, 'hand_back', return_value=True),
    }
    job_selection.reset_for_tests()
    capacity = job_processor.init_capacity()
    yield capacity, state, present, renders, api
    for gate in list(renders.finish.values()) + list(renders.hand_back_gate.values()):
        gate.set()
    job_selection.reset_for_tests()
    with job_processor._active_jobs_lock:
        job_processor._unfinished_job_ids.clear()


def _start(api, job_id):
    api['poll'].return_value = [blender_job(job_id, 'GPU')]
    thread = job_processor.get_and_claim_job(worker_id=1)
    assert thread is not None
    return thread


def test_lost_card_stops_only_its_job_and_rebuilds_after_it_released(worker, caplog):
    capacity, state, present, renders, api = worker
    assert [card.key for card in capacity.profile.cards] == [KEY_A, KEY_C]
    first, second = _start(api, 1), _start(api, 2)
    assert renders.started[1].wait(WAIT) and renders.started[2].wait(WAIT)
    assert renders.cards == {1: (KEY_A,), 2: (KEY_C,)}

    present.discard(KEY_C)
    job_processor._last_drift_check_ts = 0.0
    with caplog.at_level(logging.CRITICAL, logger='sethlans_worker_agent.capacity.card_check'):
        job_processor.maybe_assert_gpu_list_unchanged()  # the periodic check: alert, stop, rebuild request

    assert [(a['kind'], a['key']) for a in state['compute']['alerts']] == [('card_lost', KEY_C)]
    assert len([r for r in caplog.records if r.levelno == logging.CRITICAL]) == 1
    assert renders.stopped[2].wait(WAIT)
    assert not renders.stopped[1].is_set()
    assert agent_shutdown._shutdown_event.is_set() is False

    job_processor.maybe_assert_gpu_list_unchanged()  # the rebuild step: job 2 still holds card C
    assert rebuild.rebuild_waiting() is True
    assert [card.key for card in capacity.profile.cards] == [KEY_A, KEY_C]
    assert capacity.free_device_prefs() == []  # no GPU claim while the rebuild waits

    renders.hand_back_gate[2].set()
    second.join(WAIT)
    assert not second.is_alive()
    api['hand_back'].assert_called_once_with(2, blender_stop.WORKER_HARDWARE_CHANGED)

    job_processor.maybe_assert_gpu_list_unchanged()  # now the swap
    assert [card.key for card in capacity.profile.cards] == [KEY_A]
    assert rebuild.pending() is False
    assert first.is_alive() and capacity.gpu_assignments_snapshot() == {KEY_A: 1}

    renders.finish[1].set()
    first.join(WAIT)
    assert not first.is_alive()
    assert capacity.gpu_assignments_snapshot() == {}
    assert agent_shutdown._shutdown_event.is_set() is False
