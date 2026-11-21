# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Claiming Unreal jobs without Blender GPU slots (card selection O14, C-27 b).

The compute method decides Blender only. When Unreal may take a job (its
switch is on and a ticked card is usable: the Unreal poll parameters are
not empty), the poll asks for GPU jobs even without a free Blender GPU slot;
a Blender GPU job is then never claimed without one, and the Unreal job is
claimed through the hold of its one card (render per card FR-PC-CAP).
"""
import pytest

from sethlans_worker_agent import api_handler, config_unreal, job_processor, tool_manager, unreal_bridge
from sethlans_worker_agent.cards import unreal_cards

from ._fakes import blender_job, install_capacity, unreal_job

UNREAL_PARAMS = {'renderers': 'BLENDER,UNREAL', 'unreal_builds': '5.8.4-58902052'}
CARD = 'pci:0000:23:00.0'


@pytest.fixture
def manager(mocker):
    """The polls sent and the claims made; ``jobs`` is what every poll answers."""
    state = {'polls': [], 'claims': [], 'jobs': []}
    mocker.patch.object(tool_manager.tool_manager_instance, 'scan_for_local_blenders', return_value=[])
    mocker.patch.object(api_handler, 'poll_for_available_jobs',
                        side_effect=lambda params: (state['polls'].append(dict(params)), list(state['jobs']))[1])
    mocker.patch.object(api_handler, 'claim_job', side_effect=lambda job_id, worker_id: state['claims'].append(job_id)
                        or True)
    return state


@pytest.fixture
def unreal_on(monkeypatch, fake_unreal):
    """Unreal may take a job; the fake's reservation carries the Unreal card in use."""
    monkeypatch.setattr(unreal_cards, 'current_keys', lambda cached_only=True: (CARD,))
    monkeypatch.setattr(unreal_bridge, 'poll_params', lambda can_take: dict(UNREAL_PARAMS) if can_take else {})
    real = fake_unreal.try_reserve

    def with_card(job):
        reservation = real(job)
        if reservation is not None:
            reservation.card_keys, reservation.card_key = (CARD,), CARD
        return reservation
    fake_unreal.try_reserve = with_card
    return fake_unreal


def _forget(job):
    from sethlans_worker_agent import job_resources
    job_resources.forget_job(job, unfinished=True)


class TestCpuMethodWorker:

    def test_claims_the_unreal_job_and_no_blender_gpu_job(self, manager, unreal_on):
        cap = install_capacity(gpu_slot_count=0, cpu_slot_count=1)
        manager['jobs'] = [blender_job(1, device='GPU'), unreal_job(2)]

        job = job_processor.poll_and_claim_job(worker_id=1)

        assert job is not None and job['id'] == 2 and manager['claims'] == [2]
        assert set(manager['polls'][0]['device_prefs'].split(',')) == {'CPU', 'ANY', 'GPU'}
        assert manager['polls'][0]['renderers'] == 'BLENDER,UNREAL'
        assert cap.gpu_assignments_snapshot() == {CARD: 2} and cap._cpu_slot_job_id is None
        assert cap.free_device_prefs() == [] and job_processor.capacity_is_full()  # D2: no CPU job beside it
        _forget(job)

    def test_a_blender_cpu_job_is_still_taken_before_a_later_unreal_job(self, manager, unreal_on):
        install_capacity(gpu_slot_count=0, cpu_slot_count=1)
        manager['jobs'] = [blender_job(1, device='GPU'), blender_job(3, device='ANY'), unreal_job(2)]
        job = job_processor.poll_and_claim_job(worker_id=1)
        assert job['id'] == 3 and job['_reservation'].device_used == 'CPU'
        _forget(job)

    def test_without_unreal_no_gpu_job_is_asked_for(self, manager, monkeypatch, fake_unreal):
        monkeypatch.setattr(unreal_bridge, 'poll_params', lambda can_take: {})   # switch off, or no usable card
        install_capacity(gpu_slot_count=0, cpu_slot_count=1)
        manager['jobs'] = [blender_job(1, device='GPU')]
        assert job_processor.poll_and_claim_job(worker_id=1) is None
        assert manager['polls'][0]['device_prefs'] == 'CPU,ANY' and 'renderers' not in manager['polls'][0]
        assert manager['claims'] == []

    def test_a_busy_machine_asks_for_no_unreal_job(self, manager, unreal_on):
        cap = install_capacity(gpu_slot_count=0, cpu_slot_count=1)
        cap.reserve_for_job(9, 'CPU')
        assert job_processor.capacity_is_full() is True
        assert job_processor.poll_and_claim_job(worker_id=1) is None and manager['polls'] == []


class TestPendingRebuild:

    def test_no_unreal_job_is_asked_for_while_a_rebuild_is_pending(self, manager, monkeypatch, fake_unreal):
        """Concurrency review: the exclusive reservation would refuse it; no try_reserve runs for nothing."""
        from sethlans_worker_agent.capacity import rebuild
        asked = []
        monkeypatch.setattr(unreal_bridge, 'poll_params', lambda can_take: asked.append(can_take) or {})
        install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        rebuild.request_rebuild()
        job_processor.poll_and_claim_job(worker_id=1)
        assert asked == [False]


class TestNoSlotAtAll:
    """Method gpu whose only ticked card Blender does not drive: no Blender slot, Unreal still works."""

    def test_idle_machine_is_not_full_and_polls_gpu_for_unreal(self, manager, unreal_on):
        cap = install_capacity(gpu_slot_count=0, cpu_slot_count=0)
        assert job_processor.capacity_is_full() is False
        manager['jobs'] = [unreal_job(4)]
        job = job_processor.poll_and_claim_job(worker_id=1)
        assert job['id'] == 4 and manager['polls'][0]['device_prefs'] == 'GPU'
        assert cap.has_unreal_job()
        _forget(job)

    def test_nothing_is_polled_without_unreal(self, manager, monkeypatch, fake_unreal):
        monkeypatch.setattr(unreal_bridge, 'poll_params', lambda can_take: {})
        install_capacity(gpu_slot_count=0, cpu_slot_count=0)
        assert job_processor.poll_and_claim_job(worker_id=1) is None and manager['polls'] == []


class TestBlenderGpuSlots:

    def test_a_free_blender_gpu_slot_still_takes_a_blender_gpu_job(self, manager, unreal_on):
        install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        manager['jobs'] = [blender_job(1, device='GPU'), unreal_job(2)]
        job = job_processor.poll_and_claim_job(worker_id=1)
        assert job['id'] == 1 and job['_reservation'].device_used == 'GPU'
        _forget(job)


class TestSwitchOff:
    """FR-CU-6, C-27 c: with the switch off nothing asks for Unreal jobs and the report says why."""

    def test_no_unreal_poll_and_the_reason(self, monkeypatch, manager):
        from sethlans_worker_agent import unreal_switch
        from sethlans_worker_agent.unreal_executor import capabilities
        monkeypatch.setattr(config_unreal, 'ENABLED', False)
        monkeypatch.setattr(unreal_switch, 'saved', lambda: False)
        install_capacity(gpu_slot_count=0, cpu_slot_count=1)
        manager['jobs'] = [unreal_job(2)]
        assert job_processor.poll_and_claim_job(worker_id=1) is None
        assert manager['polls'][0]['device_prefs'] == 'CPU,ANY' and manager['claims'] == []
        report = unreal_bridge.snapshot()
        assert report['enabled'] is False and report['disabled_reason'] == capabilities.DISABLED_BY_CONFIG_REASON
        assert report['disabled_reason'] == 'Unreal is switched off in the worker settings'

    def test_switched_on_for_the_next_start(self, monkeypatch):
        from sethlans_worker_agent import unreal_switch
        from sethlans_worker_agent.unreal_executor import capabilities
        monkeypatch.setattr(config_unreal, 'ENABLED', False)
        monkeypatch.setattr(unreal_switch, 'saved', lambda: True)
        assert unreal_bridge.snapshot()['disabled_reason'] == capabilities.SWITCHED_ON_AT_RESTART_REASON
        assert len(capabilities.SWITCHED_ON_AT_RESTART_REASON) <= capabilities.MAX_REASON_LENGTH
