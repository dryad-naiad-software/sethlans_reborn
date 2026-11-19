# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The render script for slots built from the card choice, run against a fake
``bpy`` (AC-5, AC-6, AC-43), and the device of the EXR thumbnail pass and the
executor taken from the reservation (FR-CB-1).

Blender lists one entry per backend (an RTX card is a CUDA and an OPTIX
entry); the detection cache, and so the inventory, lists one id per card.
"""
import pytest

from sethlans_worker_agent import blender_executor, blender_thumbnail
from sethlans_worker_agent.gpu_device_script import GPU_CHECK_EXIT_CODE
from sethlans_worker_agent.render_script import generate_render_config_script
from tests.unit.worker._card_choice import (
    IGPU_ID, KEY_IGPU, KEY_RTX_A, KEY_RTX_B, KEY_XT_03, KEY_XT_23, RTX_A_ID, RTX_B_ID, XT_03_ID, XT_23_ID,
    capacity_for, card, igpu, inv, rtx_a, rtx_b, section, xt_03, xt_23,
)
from tests.unit.worker._fake_bpy import CPU, FakePrefs, check_failures, install_fake_bpy, list_mismatches, run_script

RTX_A_CUDA = ('CUDA', RTX_A_ID[:-len('_OptiX')], 'NVIDIA GeForce RTX 4080')
RTX_A_OPTIX = ('OPTIX', RTX_A_ID, 'NVIDIA GeForce RTX 4080')
RTX_B_CUDA = ('CUDA', RTX_B_ID[:-len('_OptiX')], 'NVIDIA GeForce RTX 4080')
RTX_B_OPTIX = ('OPTIX', RTX_B_ID, 'NVIDIA GeForce RTX 4080')
XT_03 = ('HIP', XT_03_ID, 'AMD Radeon RX 7900 XT')
XT_23 = ('HIP', XT_23_ID, 'AMD Radeon RX 7900 XT')
IGPU = ('HIP', IGPU_ID, 'AMD Radeon(TM) Graphics')

GPU_SELECT = '_sethlans_select_gpus('
CPU_LINE = "bpy.context.scene.cycles.device = 'CPU'"


def _run(monkeypatch, reservation, specs):
    prefs = FakePrefs(specs)
    bpy = install_fake_bpy(monkeypatch, prefs)
    run_script(generate_render_config_script(238, 'CYCLES', 'GPU', {}, reservation=reservation))
    return prefs, bpy


def _on(spec):
    return (spec[0], spec[1])


class TestSplitSlot:
    """AC-5: three cards detected, two ticked."""

    @pytest.fixture
    def slot_1(self):
        cards = inv(rtx_a(), rtx_b(), igpu())
        cap = capacity_for(section('gpu', 'split', [KEY_RTX_A, KEY_RTX_B], cards), cards)
        cap.reserve_for_job(237, 'GPU')
        return cap.reserve_for_job(238, 'GPU')

    def test_slot_1_enables_only_its_card(self, monkeypatch, capsys, slot_1):
        prefs, bpy = _run(monkeypatch, slot_1, [RTX_A_CUDA, RTX_B_CUDA, CPU, RTX_A_OPTIX, RTX_B_OPTIX, IGPU])

        assert slot_1.card_keys == (KEY_RTX_B,)
        assert prefs.enabled() == [_on(RTX_B_OPTIX)]
        assert next(d for d in prefs.devices if d.id == IGPU_ID).use is False
        assert bpy.context.scene.cycles.device == 'GPU'
        err = capsys.readouterr().err
        assert check_failures(err) == []
        assert list_mismatches(err) == []  # the unticked card is ignored, not drift

    def test_ignored_carries_only_the_unticked_card(self, slot_1):
        assert slot_1.ignored == (IGPU_ID,)


class TestCombinedSlot:
    """AC-6 and AC-43: ticked AMD cards and an unticked NVIDIA card."""

    @pytest.fixture
    def combined(self):
        cards = inv(rtx_a(), xt_03(), xt_23())
        cap = capacity_for(section('gpu', 'combined', [KEY_XT_03, KEY_XT_23], cards), cards)
        return cap.reserve_for_job(238, 'GPU')

    def test_uses_hip_on_the_cards_in_use_only(self, monkeypatch, capsys, combined):
        prefs, bpy = _run(monkeypatch, combined, [RTX_A_CUDA, CPU, RTX_A_OPTIX, XT_03, XT_23])

        assert prefs.compute_device_type == 'HIP'
        assert sorted(prefs.enabled()) == sorted([_on(XT_03), _on(XT_23)])
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''  # neither NVIDIA entry is reported

    def test_a_card_in_use_without_its_entry_exits_87(self, monkeypatch, capsys, combined):
        with pytest.raises(SystemExit) as caught:
            _run(monkeypatch, combined, [RTX_A_CUDA, CPU, RTX_A_OPTIX, XT_03])
        assert caught.value.code == GPU_CHECK_EXIT_CODE
        assert len(check_failures(capsys.readouterr().err)) == 1

    def test_a_card_in_use_with_two_entries_exits_87(self, monkeypatch, capsys, combined):
        with pytest.raises(SystemExit) as caught:
            _run(monkeypatch, combined, [CPU, XT_03, XT_23, XT_23])
        assert caught.value.code == GPU_CHECK_EXIT_CODE
        assert len(check_failures(capsys.readouterr().err)) == 1

    def test_an_unticked_card_of_the_same_backend_stays_off(self, monkeypatch, capsys):
        cards = inv(xt_03(), xt_23(), igpu())
        cap = capacity_for(section('gpu', 'combined', [KEY_XT_03, KEY_XT_23], cards), cards)

        prefs, _bpy = _run(monkeypatch, cap.reserve_for_job(238, 'GPU'), [CPU, XT_03, XT_23, IGPU])

        assert sorted(prefs.enabled()) == sorted([_on(XT_03), _on(XT_23)])
        assert KEY_IGPU not in cap.profile.card_keys
        assert capsys.readouterr().err == ''


GTX_ID = 'CUDA_NVIDIA GeForce GTX 1080_0000:05:00'
GTX_CUDA = ('CUDA', GTX_ID, 'NVIDIA GeForce GTX 1080')
GTX_OPTIX = ('OPTIX', GTX_ID + '_OptiX', 'NVIDIA GeForce GTX 1080')


def _gtx():
    return card('pci:0000:05:00.0', 'NVIDIA GeForce GTX 1080', GTX_ID, 'CUDA', 0x10DE, 0x1B80)


class TestNvidiaBackend:
    """Combined CUDA and OptiX cards render through CUDA; OptiX alone stays OptiX."""

    def _slot(self, mode, cards, ticked):
        cap = capacity_for(section('gpu', mode, ticked, inv(*cards)), inv(*cards))
        return cap.reserve_for_job(238, 'GPU')

    def test_cuda_and_optix_mix_enables_the_cuda_entries(self, monkeypatch, capsys):
        slot = self._slot('combined', (rtx_a(), _gtx()), [KEY_RTX_A, _gtx().key])

        prefs, bpy = _run(monkeypatch, slot, [RTX_A_CUDA, GTX_CUDA, CPU, RTX_A_OPTIX, GTX_OPTIX])

        assert prefs.compute_device_type == 'CUDA'
        assert prefs.enabled() == [_on(RTX_A_CUDA), _on(GTX_CUDA)]
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''

    def test_cuda_and_optix_mix_without_a_cuda_entry_exits_87(self, monkeypatch, capsys):
        slot = self._slot('combined', (rtx_a(), _gtx()), [KEY_RTX_A, _gtx().key])

        with pytest.raises(SystemExit) as caught:
            _run(monkeypatch, slot, [GTX_CUDA, CPU, RTX_A_OPTIX, GTX_OPTIX])

        assert caught.value.code == GPU_CHECK_EXIT_CODE
        failures = check_failures(capsys.readouterr().err)
        assert len(failures) == 1
        assert repr(RTX_A_CUDA[1]) in failures[0]

    def test_pure_optix_combined_stays_optix(self, monkeypatch, capsys):
        slot = self._slot('combined', (rtx_a(), rtx_b()), [KEY_RTX_A, KEY_RTX_B])

        prefs, _bpy = _run(monkeypatch, slot, [RTX_A_CUDA, RTX_B_CUDA, CPU, RTX_A_OPTIX, RTX_B_OPTIX])

        assert prefs.compute_device_type == 'OPTIX'
        assert prefs.enabled() == [_on(RTX_A_OPTIX), _on(RTX_B_OPTIX)]
        assert capsys.readouterr().err == ''

    def test_single_optix_card_isolated_stays_optix(self, monkeypatch, capsys):
        slot = self._slot('split', (rtx_a(), _gtx()), [KEY_RTX_A, _gtx().key])

        prefs, _bpy = _run(monkeypatch, slot, [RTX_A_CUDA, GTX_CUDA, CPU, RTX_A_OPTIX, GTX_OPTIX])

        assert slot.card_keys == (KEY_RTX_A,)
        assert prefs.compute_device_type == 'OPTIX'
        assert prefs.enabled() == [_on(RTX_A_OPTIX)]
        assert capsys.readouterr().err == ''


def _fake_process(mocker, return_code=0):
    proc = mocker.Mock(pid=4242)
    proc.poll.return_value = return_code
    proc.wait.return_value = return_code
    proc.stdout.readline = mocker.Mock(return_value='')
    proc.stderr.readline = mocker.Mock(return_value='')
    return proc


class TestThumbnailAndExecutorDevice:
    """FR-CB-1: the EXR thumbnail and the render take the device from the reservation."""

    @pytest.fixture
    def capacity(self):
        cards = inv(xt_03(), xt_23())
        return capacity_for(section('cpu_gpu', 'combined', [KEY_XT_03, KEY_XT_23], cards), cards)

    @pytest.fixture
    def scripts(self, mocker, tmp_path):
        mocker.patch('sethlans_worker_agent.config.WORKER_TEMP_DIR', str(tmp_path / 'temp'))
        mocker.patch('sethlans_worker_agent.config.WORKER_OUTPUT_DIR', str(tmp_path / 'output'))
        mocker.patch('sethlans_worker_agent.config.WORKER_ROOT', str(tmp_path))
        mocker.patch('sethlans_worker_agent.config.CPU_THREADS', 0)
        mocker.patch('subprocess.Popen', return_value=_fake_process(mocker))
        written = []

        def capture(*args, **kwargs):
            written.append(generate_render_config_script(*args, **kwargs))
            return written[-1]

        for module in (blender_thumbnail, blender_executor):
            mocker.patch.object(module, 'generate_render_config_script', side_effect=capture)
        return written

    def _thumbnail(self, tmp_path, reservation):
        job = {'id': 5, 'render_engine': 'CYCLES', 'render_device': 'ANY',
               'render_settings': {'render.image_settings.file_format': 'OPEN_EXR'}}
        blender_thumbnail.render_exr_hdr_thumbnail(job, str(tmp_path / 'blender'), str(tmp_path / 'scene.blend'), 1,
                                                   str(tmp_path / 'output' / 'render_####.exr'), reservation)

    def test_thumbnail_on_the_cpu_slot_enables_no_card(self, capacity, scripts, tmp_path):
        """AC-43: today's code enabled every card for it."""
        capacity.reserve_for_job(4, 'GPU')
        self._thumbnail(tmp_path, capacity.reserve_for_job(5, 'ANY'))
        assert len(scripts) == 1
        assert CPU_LINE in scripts[0]
        assert GPU_SELECT not in scripts[0]

    def test_thumbnail_on_the_combined_slot_uses_its_cards(self, capacity, scripts, tmp_path):
        self._thumbnail(tmp_path, capacity.reserve_for_job(5, 'ANY'))
        assert GPU_SELECT in scripts[0]
        assert "'HIP', 'combined'," in scripts[0]

    def test_any_job_on_the_combined_slot_renders_on_the_gpu(self, mocker, capacity, scripts, tmp_path):
        mocker.patch('sethlans_worker_agent.asset_manager.ensure_asset_is_available',
                     return_value=str(tmp_path / 'scene.blend'))
        manager = 'sethlans_worker_agent.tool_manager.tool_manager_instance'
        mocker.patch(f'{manager}.ensure_blender_version_available', return_value=str(tmp_path / 'blender'))
        mocker.patch(f'{manager}.acquire_version')
        mocker.patch(f'{manager}.release_version')
        mocker.patch('sethlans_worker_agent.api_handler.get_job_status', return_value='RENDERING')
        job = {'id': 6, 'name': 'any', 'render_device': 'ANY', 'render_engine': 'CYCLES', 'blender_version': '4.5.0',
               'start_frame': 1, 'end_frame': 1, 'output_file_pattern': 'render/frame_####.png',
               'asset': {'blend_file': 'https://127.0.0.1:8080/media/assets/scene.blend'}, 'render_settings': {}}

        result = blender_executor.execute_blender_job(job, reservation=capacity.reserve_for_job(6, 'ANY'))

        assert result[0] is True
        assert len(scripts) == 1
        assert GPU_SELECT in scripts[0]
        assert CPU_LINE not in scripts[0]
