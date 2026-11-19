# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The periodic GPU check keeps running when its detection Blender is removed (#253).

The version sync removes old Blender versions, including the one that
produced the GPU list in use. The check then detects with another installed
Blender: when it still offers every card in use it is adopted as the
detection Blender; when it does not, the card it no longer offers is held
from Blender (``card_lost_blender``) and nothing is adopted. A detection that
cannot run or whose output cannot be read is "try again later"
(``drift.DETECTION_FAILED``), never an exception out of the agent loop's check.
"""
import json
import logging
import subprocess
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import agent_shutdown, hardware_detection, job_processor
from sethlans_worker_agent.capacity import (
    CapacityProfile,
    WorkerCapacity,
    card_blender,
    card_check,
    drift as drift_module,
    rebuild,
)
from sethlans_worker_agent.cards import os_probe
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker.executors._fakes import fake_cards

DRIFT = drift_module.__name__
CARD = fake_cards(1)[0]
HIP = {'index': 0, 'name': CARD.name, 'type': CARD.backend, 'id': CARD.blender_id}
OTHER_HIP = {'index': 0, 'name': 'Other', 'type': CARD.backend, 'id': 'CUDA_Other_0000:09:00'}


def _capacity(gpu_slot_count=1, startup_gpu_count=1):
    return WorkerCapacity(CapacityProfile(
        gpu_slot_count=gpu_slot_count, cpu_slot_count=1, cpu_thread_ceiling=15,
        cpu_threads_effective=15, cards=fake_cards(startup_gpu_count), gpu_mode='split', method='cpu_gpu',
    ))


@pytest.fixture
def machine(monkeypatch):
    """One ticked card in use; the real uncached detection (its subprocess is mocked by each fixture)."""
    fake = CardEnv(monkeypatch, count=1)
    monkeypatch.setattr(card_check, 'detect_physical_gpus_now', drift_module.detect_physical_gpus_now)
    return fake


@pytest.fixture
def env(mocker, monkeypatch, tmp_path, machine):
    """The list in use came from a Blender that is gone; another one is installed."""
    gone = tmp_path / 'blender-4.2.3-windows-x64' / 'blender.exe'
    other = tmp_path / 'blender-5.0.1-windows-x64' / 'blender.exe'
    other.parent.mkdir()
    other.write_text('')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(gone))
    find = mocker.patch(
        'sethlans_worker_agent.capacity.drift._choose_fallback_blender', return_value=str(other),
    )
    run = mocker.patch(
        'sethlans_worker_agent.capacity.drift.subprocess.run',
        return_value=mocker.MagicMock(stdout=json.dumps([HIP]) + '\n'),
    )
    return SimpleNamespace(gone=str(gone), other=str(other), find=find, run=run, machine=machine, cap=_capacity())


def _assert_no_action(env):
    assert env.machine.alerts() == []
    assert rebuild.pending() is False
    assert agent_shutdown._shutdown_event.is_set() is False


def _messages(caplog, level, text):
    return [r.getMessage() for r in caplog.records if r.levelno == level and text in r.getMessage()]


class TestRememberedBlenderRemoved:

    def test_same_list_adopts_the_other_blender(self, env, caplog):
        with caplog.at_level(logging.DEBUG, logger=DRIFT):
            assert env.cap.assert_gpu_list_unchanged() is None

        assert env.run.call_args.args[0][0] == env.other
        _assert_no_action(env)
        assert hardware_detection._gpu_details_blender_exe == env.other
        adopted = _messages(caplog, logging.INFO, 'from now on')
        assert len(adopted) == 1
        assert ascii(env.other) in adopted[0]
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_next_check_uses_the_adopted_blender_directly(self, env, caplog):
        env.cap.assert_gpu_list_unchanged()
        finds_after_first = env.find.call_count
        caplog.clear()

        with caplog.at_level(logging.DEBUG, logger=DRIFT):
            assert env.cap.assert_gpu_list_unchanged() is None

        assert env.run.call_count == 2
        assert env.run.call_args.args[0][0] == env.other
        assert env.find.call_count == finds_after_first  # no fallback search
        assert _messages(caplog, logging.INFO, 'no longer installed') == []
        assert _messages(caplog, logging.INFO, 'from now on') == []
        _assert_no_action(env)

    def test_list_without_the_card_is_not_trusted_and_adopts_nothing(self, env, mocker, caplog):
        """Another Blender version may name devices differently: no blender_lost, one WARNING, list kept."""
        env.run.return_value = mocker.MagicMock(stdout=json.dumps([OTHER_HIP]) + '\n')

        with caplog.at_level(logging.DEBUG):
            for _ in range(2):
                assert env.cap.assert_gpu_list_unchanged() is None

        assert env.run.call_args.args[0][0] == env.other
        assert env.machine.alerts() == [] and rebuild.pending() is False
        assert card_blender.known()[0] == env.machine.blender_records()  # the startup list, not the fallback's
        warnings = _messages(caplog, logging.WARNING, 'does not offer')
        assert warnings == [f"Blender 5.0.1 does not offer 'GPU 0' ({CARD.key}); the device list in use is kept."]
        assert agent_shutdown._shutdown_event.is_set() is False  # no exit (FR-CL-6)
        assert hardware_detection._gpu_details_blender_exe == env.gone
        assert _messages(caplog, logging.INFO, 'from now on') == []

    def test_fallback_offering_every_card_is_trusted_with_its_version(self, env):
        assert drift_module.detect_physical_gpus_now() == [(CARD.blender_id, CARD.backend)]
        os_cards = os_probe.probe()

        records, version, trusted = card_blender.from_detection(
            [(CARD.blender_id, CARD.backend)], os_cards, env.cap.profile)

        assert (records, version, trusted) == ([{'id': CARD.blender_id, 'type': CARD.backend}], '5.0.1', True)
        assert card_blender.known() == (records, '4.5.1')  # trusted: the list a later rebuild uses

    def test_no_blender_installed_tries_again_later(self, env, caplog):
        env.find.return_value = None

        with caplog.at_level(logging.DEBUG, logger=DRIFT):
            for _ in range(3):
                assert env.cap.assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED

        env.run.assert_not_called()
        _assert_no_action(env)
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
        assert hardware_detection._gpu_details_blender_exe == env.gone


DETECTION_ERRORS = [
    OSError(8, 'Exec format error'),
    FileNotFoundError(2, 'No such file or directory'),
    UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte'),
]
DETECTION_ERROR_IDS = ['os-error', 'file-not-found', 'undecodable-output']
# Parseable JSON whose records _filter_preferred_gpus cannot use.
MALFORMED_OUTPUTS = [
    '[1]\n',
    '[{"index": 0, "name": "AMD Radeon", "id": "HIP_A"}]\n',
    '[{"name": "AMD Radeon", "type": "HIP", "id": "HIP_A"}]\n',
    '[{"index": 0, "name": "AMD Radeon", "type": "HIP", "id": 7}]\n',
]
MALFORMED_IDS = ['not-a-dict', 'no-type', 'no-index', 'id-not-a-string']


@pytest.fixture
def remembered(mocker, monkeypatch, tmp_path, machine):
    """The Blender that produced the list in use is installed; returns the run mock."""
    exe = tmp_path / 'blender.exe'
    exe.write_text('')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(exe))
    return SimpleNamespace(run=mocker.patch('sethlans_worker_agent.capacity.drift.subprocess.run'),
                           machine=machine)


def _no_action(remembered):
    return remembered.machine.alerts() == [] and rebuild.pending() is False


class TestDetectionThatCannotRun:

    @pytest.mark.parametrize('error', DETECTION_ERRORS, ids=DETECTION_ERROR_IDS)
    def test_detect_returns_none(self, remembered, error):
        remembered.run.side_effect = error
        assert drift_module.detect_physical_gpus_now() is None

    @pytest.mark.parametrize('error', DETECTION_ERRORS, ids=DETECTION_ERROR_IDS)
    def test_comparison_reports_detection_failed(self, remembered, error):
        remembered.run.side_effect = error

        assert _capacity().assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED

        assert _no_action(remembered)

    @pytest.mark.parametrize('error', DETECTION_ERRORS, ids=DETECTION_ERROR_IDS)
    def test_agent_loop_check_does_not_raise(self, remembered, mocker, error):
        remembered.run.side_effect = error
        job_processor._capacity = _capacity()
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
        mocker.patch('sethlans_worker_agent.job_processor.time.monotonic', return_value=1030.0)
        job_processor._last_drift_check_ts = 1000.0

        job_processor.maybe_assert_gpu_list_unchanged()

        remembered.run.assert_called_once()
        assert _no_action(remembered)
        assert job_processor._last_drift_check_ts == 1030.0

    @pytest.mark.parametrize('stdout', MALFORMED_OUTPUTS, ids=MALFORMED_IDS)
    def test_malformed_device_record_tries_again_later(self, remembered, mocker, stdout):
        remembered.run.return_value = mocker.MagicMock(stdout=stdout)

        assert drift_module.detect_physical_gpus_now() is None
        assert _capacity().assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED
        assert _no_action(remembered)

    def test_output_is_decoded_as_utf8_with_replacement(self, remembered, mocker):
        remembered.run.return_value = mocker.MagicMock(stdout=json.dumps([HIP]) + '\n')

        assert drift_module.detect_physical_gpus_now() == [(CARD.blender_id, CARD.backend)]

        kwargs = remembered.run.call_args.kwargs
        assert (kwargs['text'], kwargs['encoding'], kwargs['errors']) == (True, 'utf-8', 'replace')


class TestComparisonOutcome:

    def test_detection_failed_value(self, monkeypatch):
        fake = CardEnv(monkeypatch, count=1)
        fake.fresh = None
        assert _capacity().assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED

    def test_unchanged_list_returns_none(self, monkeypatch):
        CardEnv(monkeypatch, count=1)
        assert _capacity().assert_gpu_list_unchanged() is None
        assert rebuild.pending() is False

    @pytest.mark.parametrize('method, ticked, detections', [
        ('cpu', (0, 1), 0), ('gpu', (), 0), ('gpu', (0, 1), 1), ('cpu_gpu', (1,), 1),
    ], ids=['method-cpu', 'nothing-ticked', 'gpu-ticked', 'cpu-gpu-ticked'])
    def test_empty_profile_detects_only_when_ticks_include_gpu(self, monkeypatch, method, ticked, detections):
        """With no Blender card in use the detection runs when the method includes GPU and a card is ticked."""
        fake = CardEnv(monkeypatch, count=2, method=method, ticked=ticked)

        assert _capacity(gpu_slot_count=0, startup_gpu_count=0).assert_gpu_list_unchanged() is None

        assert fake.detections == detections and fake.alerts() == []


def test_subprocess_error_family_still_tries_again_later(remembered):
    """CalledProcessError and TimeoutExpired stay "try again later" (#49)."""
    for error in (subprocess.CalledProcessError(1, 'blender'), subprocess.TimeoutExpired('blender', 90)):
        remembered.run.side_effect = error
        assert _capacity().assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED
