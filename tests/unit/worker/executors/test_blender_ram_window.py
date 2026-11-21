# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender jobs in the Unreal RAM gate (render per card D9, FR-PC-RAM-5, FR-PC-RAM-6): a claimed Blender
job is noted in the RAM ledger (it counts ``ram_blender_mb`` for 300 s) and dropped at its release; an
Unreal claim is not noted; a Blender job beside a running Unreal job is claimed only when the RAM
left above the reserves covers what the Unreal claims may still take.
"""
from sethlans_worker_agent import job_processor, job_resources, job_selection, unreal_bridge
from sethlans_worker_agent.unreal_executor import ram_ledger, reserve_local

from ._fakes import blender_job, install_capacity, key, unreal_job


def _poll_returns(mocker, jobs):
    mocker.patch('sethlans_worker_agent.job_processor._build_poll_params', return_value={})
    mocker.patch('sethlans_worker_agent.api_handler.poll_for_available_jobs', return_value=jobs)
    mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)


class TestBlenderClaims:

    def test_a_claimed_blender_job_is_noted_until_its_release(self, mocker):
        cap = install_capacity()
        _poll_returns(mocker, [blender_job(3)])
        job = job_processor.poll_and_claim_job(worker_id=1)
        assert set(ram_ledger.ledger._blender) == {3}
        assert job_resources.release_reservations(3, job['_renderer_reservation'], cap) is True
        assert ram_ledger.ledger._blender == {} and not ram_ledger.ledger.has_claims()

    def test_an_unreal_claim_is_not_noted_as_blender(self, mocker, fake_unreal):
        install_capacity(gpu_slot_count=2)
        _poll_returns(mocker, [unreal_job(7)])
        assert job_processor.poll_and_claim_job(worker_id=1) is not None
        assert ram_ledger.ledger._blender == {}

    def test_a_refused_blender_claim_is_not_noted(self, mocker):
        install_capacity()
        _poll_returns(mocker, [blender_job(4)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=False)
        assert job_processor.poll_and_claim_job(worker_id=1) is None
        assert ram_ledger.ledger._blender == {}


class TestBlenderBesideUnreal:

    def test_no_check_without_an_unreal_job(self, mocker):
        check = mocker.patch.object(unreal_bridge, 'blender_ram_block', return_value='no room')
        assert job_selection.select_claimable_job([blender_job(1)], install_capacity()) is not None
        check.assert_not_called()

    def test_skipped_while_ram_does_not_allow_it(self, mocker, caplog):
        cap = install_capacity(gpu_slot_count=2)
        cap.reserve_unreal_card(9, key(0))
        mocker.patch.object(unreal_bridge, 'blender_ram_block', return_value='no room')
        caplog.set_level('INFO', logger='sethlans_worker_agent.job_selection')
        assert job_selection.select_claimable_job([blender_job(1)], cap) is None
        assert 'Skipping BLENDER job 1: RAM beside the running Unreal job: no room' in caplog.text

    def test_claimed_when_it_fits(self, mocker):
        cap = install_capacity(gpu_slot_count=2)
        cap.reserve_unreal_card(9, key(0))
        mocker.patch.object(unreal_bridge, 'blender_ram_block', return_value='')
        assert job_selection.select_claimable_job([blender_job(1)], cap).job['id'] == 1

    def test_the_bridge_checks_the_workers_ledger(self, monkeypatch):
        monkeypatch.setattr(reserve_local, 'available_mb', lambda: 4096 + 16384 + 4096)  # + its own 4096
        monkeypatch.setattr(reserve_local, 'commit_headroom_mb', lambda: 1 << 20)
        ram_ledger.ledger.register('unreal-claim', 16384, 40944)
        assert unreal_bridge.blender_ram_block() == ''
        ram_ledger.ledger.register_blender(5)   # another 4096 MB on its way
        assert 'physical RAM' in unreal_bridge.blender_ram_block()

    def test_a_broken_check_blocks_and_logs_once(self, monkeypatch, caplog):
        def broken(ledger):
            raise RuntimeError('bug')
        monkeypatch.setattr(reserve_local, 'blender_fits', broken)
        assert unreal_bridge.blender_ram_block() and unreal_bridge.blender_ram_block()
        assert caplog.text.count('Unreal support failed in blender_ram_block') == 1
