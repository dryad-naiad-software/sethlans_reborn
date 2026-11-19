# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
From the saved card choice to the device entries a render enables (card selection, chunk 2).

Each job goes through the real worker path: ``get_and_claim_job`` polls a
mocked manager, ``job_selection`` and ``job_claim`` reserve a slot on the
capacity built from the saved choice, the job thread runs the Blender
executor, ``render_script`` writes the settings script from the
reservation, and a fake Blender (``FakeBlender``) executes that script
against a fake ``bpy`` holding one entry per card. Covered: FR-CB-1 (render
inputs from the reservation, ``device_used`` decides CPU or GPU, B1),
FR-CB-2 (unticked cards switched off) and FR-CB-3 (a GPU job with no card
in use fails before Blender starts and never renders on the CPU).
"""

import pytest

from sethlans_worker_agent import config_compute, job_processor, job_selection
from sethlans_worker_agent.cards.in_use import NO_TICK_TEXT, NONE_USABLE_TEXT
from sethlans_worker_agent.job_lifecycle import process_claimed_job

from ._card_choice_harness import (
    ID_A, ID_C, KEY_A, KEY_C, blender_job, final_status, install_card_choice, install_render_path,
)


@pytest.fixture(autouse=True)
def _clean_claim_records():
    job_selection.reset_for_tests()
    yield
    job_selection.reset_for_tests()


def _dispatch(api, job):
    """Poll once with ``job`` on offer; return the started job thread."""
    api['poll'].return_value = [job]
    thread = job_processor.get_and_claim_job(worker_id=1)
    assert thread is not None, f"job {job['id']} was not claimed"
    return thread


def _join(*threads):
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()


class TestCardRenderPathIntegration:
    """FR-CB-1, FR-CB-2: the script enables exactly the cards the reservation holds."""

    def test_split_jobs_render_each_on_its_own_reserved_card(self, mocker, monkeypatch, tmp_path):
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_A, KEY_C))
        blender, api = install_render_path(mocker, monkeypatch, tmp_path, overlap=2)

        # Both renders wait for each other inside Blender, so both slots are held at once.
        first = _dispatch(api, blender_job(1, 'GPU'))
        second = _dispatch(api, blender_job(2, 'GPU'))
        _join(first, second)

        assert blender.runs[1]['enabled'] == [('CUDA', ID_A)]
        assert blender.runs[2]['enabled'] == [('CUDA', ID_C)]
        for job_id in (1, 2):
            run = blender.runs[job_id]
            assert (run['device'], run['compute_type']) == ('GPU', 'CUDA')
            assert '--threads' not in run['command']
            assert final_status(api, job_id)['status'] == 'DONE'
        assert capacity.has_no_reservations()

    def test_unticked_card_is_never_enabled(self, mocker, monkeypatch, tmp_path):
        install_card_choice(mocker, method='gpu', ticked=(KEY_C,))
        blender, api = install_render_path(mocker, monkeypatch, tmp_path)

        _join(_dispatch(api, blender_job(1, 'GPU')))

        # Blender enables new devices by default: the script must switch A and B off.
        assert blender.runs[1]['enabled'] == [('CUDA', ID_C)]
        assert final_status(api, 1)['status'] == 'DONE'

    @pytest.mark.parametrize('method', ['gpu', 'cpu_gpu'])
    def test_any_job_on_combined_slot_renders_on_gpu(self, mocker, monkeypatch, tmp_path, method):
        capacity, _probe = install_card_choice(mocker, method=method, gpu_mode='combined', ticked=(KEY_A, KEY_C))
        blender, api = install_render_path(mocker, monkeypatch, tmp_path)
        seen = {}
        reserve = capacity.reserve_for_job

        def spy(job_id, device_pref):
            seen[job_id] = reserve(job_id, device_pref)
            return seen[job_id]

        mocker.patch.object(capacity, 'reserve_for_job', side_effect=spy)
        _join(_dispatch(api, blender_job(5, 'ANY')))

        assert seen[5].device_used == 'GPU' and seen[5].card_keys == (KEY_A, KEY_C)
        run = blender.runs[5]
        assert (run['device'], run['compute_type']) == ('GPU', 'CUDA')
        assert run['enabled'] == [('CUDA', ID_A), ('CUDA', ID_C)]
        assert '--threads' not in run['command']
        assert final_status(api, 5)['status'] == 'DONE'

    def test_cpu_job_renders_on_cpu_with_thread_cap(self, mocker, monkeypatch, tmp_path):
        install_card_choice(mocker, method='cpu_gpu', ticked=(KEY_A, KEY_C))
        blender, api = install_render_path(mocker, monkeypatch, tmp_path)

        _join(_dispatch(api, blender_job(7, 'CPU')))

        run = blender.runs[7]
        assert run['device'] == 'CPU'
        assert run['compute_type'] == 'NONE'  # the script never touched the GPU preferences
        assert '--threads' in run['command']
        assert final_status(api, 7)['status'] == 'DONE'


class TestGpuJobWithoutCardIntegration:
    """FR-CB-3 / FR-CK-6: method ``gpu`` with no Blender card in use."""

    def test_ticked_card_missing_takes_no_job(self, mocker, monkeypatch, tmp_path):
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_A,), present=(KEY_C,))
        blender, api = install_render_path(mocker, monkeypatch, tmp_path)

        profile = capacity.profile
        assert (profile.gpu_slot_count, profile.cpu_slot_count) == (0, 0)
        assert profile.problem == NONE_USABLE_TEXT
        api['poll'].return_value = [blender_job(1, 'GPU')]
        assert job_processor.get_and_claim_job(worker_id=1) is None
        api['poll'].assert_not_called()
        api['claim'].assert_not_called()
        assert blender.launches == 0

    def test_no_card_ticked_through_environment_takes_no_job(self, mocker, monkeypatch, tmp_path):
        monkeypatch.setenv(config_compute.ENV_METHOD, 'gpu')
        capacity, _probe = install_card_choice(mocker, method='cpu')
        _blender, api = install_render_path(mocker, monkeypatch, tmp_path)

        assert capacity.profile.method == 'gpu'
        assert capacity.profile.problem == NO_TICK_TEXT
        assert capacity.free_device_prefs() == []
        assert job_processor.get_and_claim_job(worker_id=1) is None
        api['poll'].assert_not_called()

    def test_gpu_job_without_card_fails_before_blender_starts(self, mocker, monkeypatch, tmp_path):
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_A,), present=(KEY_C,))
        blender, api = install_render_path(mocker, monkeypatch, tmp_path)
        job = blender_job(9, 'GPU')
        job['_reservation'] = None  # this worker has no GPU slot to give it

        process_claimed_job(job)

        api['popen'].assert_not_called()  # neither on a card nor on the CPU
        assert blender.runs == {}
        payload = final_status(api, 9)
        assert payload['status'] == 'ERROR'
        assert NONE_USABLE_TEXT in payload['error_message']
        api['upload'].assert_not_called()
        assert capacity.has_no_reservations()
