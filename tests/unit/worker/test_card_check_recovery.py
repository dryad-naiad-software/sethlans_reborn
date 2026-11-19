# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Card check corner cases from the chunk 4 reviews (FR-CK-1, FR-CK-2, FR-CK-7, FR-CL-4, FR-CB-5).

- A card held from Blender after a driver update comes back once Blender
  offers it again and the alert is dismissed, with no Blender card left in
  use meanwhile and without a restart.
- A check that probed nothing forgets the Eevee/Workbench condition.
- A rebuild never acts on a failed OS probe once the probe has worked in
  this process; a machine whose probe never works still rebuilds.
- No rebuild (and no OS probe) while an Unreal job holds the machine.
- The card GET shows hand-edited alert and card texts normalised.
"""
import pytest

from sethlans_worker_agent import job_processor, system_monitor
from sethlans_worker_agent.capacity import WorkerCapacity, card_check, rebuild
from sethlans_worker_agent.cards import choice
from sethlans_worker_agent.cards import warnings as card_warnings
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker._card_choice import KEY_XT_23
from tests.unit.worker._card_pages import RUNTIME_GET, call, fake_probes, fake_saved
from tests.unit.worker.executors._fakes import fake_cards, key, profile


@pytest.fixture
def host(mocker):
    mocker.patch.object(system_monitor, 'get_gpu_device_details', return_value=[])
    mocker.patch.object(system_monitor, 'get_cpu_thread_count', return_value=8)


class TestDriverUpdate:

    def test_card_comes_back_after_detection_and_dismissal_without_a_restart(self, monkeypatch, host):
        machine = CardEnv(monkeypatch, count=1, method='gpu')
        cap = job_processor.init_capacity()
        assert cap.profile.cards == fake_cards(1)

        machine.fresh = []  # during the driver update Blender lists no device (exit 0, empty list)
        cap.assert_gpu_list_unchanged()
        assert machine.alert_keys() == [('card_lost_blender', key(0))]
        assert rebuild.step(cap) == rebuild.SWAPPED and cap.profile.cards == ()

        machine.fresh = 'present'  # the driver works again
        assert cap.assert_gpu_list_unchanged() is None
        assert machine.detections == 2  # it still runs with no Blender card in use
        assert cap.profile.cards == () and rebuild.pending() is False  # held until a person dismisses it

        choice.dismiss_alerts([machine.alerts()[0]['id']], 'manager')
        assert rebuild.step(cap) == rebuild.SWAPPED
        assert cap.profile.cards == fake_cards(1) and cap.profile.gpu_slot_count == 1

    def test_empty_profile_detection_raises_no_new_blender_alert(self, monkeypatch):
        machine = CardEnv(monkeypatch, count=2, method='gpu')
        machine.fresh = machine.blender_records({key(0)})
        cap = WorkerCapacity(profile(gpu_slot_count=0))

        cap.assert_gpu_list_unchanged()

        assert machine.detections == 1 and machine.alerts() == []


class TestEngineConditionWithoutProbe:

    def test_check_without_ticks_forgets_the_condition(self, monkeypatch):
        CardEnv(monkeypatch, count=1, method='cpu', ticked=())
        card_warnings.reset_for_tests(True)

        WorkerCapacity(profile(gpu_slot_count=0)).assert_gpu_list_unchanged()

        assert card_warnings._condition is None

    def test_start_without_ticks_forgets_the_condition(self, monkeypatch, host):
        CardEnv(monkeypatch, count=1, method='cpu', ticked=())
        card_warnings.reset_for_tests(True)

        job_processor.init_capacity()

        assert card_warnings._condition is None


class TestRebuildOnFailedProbe:

    def test_failed_probe_after_a_working_one_stays_pending(self, monkeypatch):
        machine = CardEnv(monkeypatch, count=2)
        cap = WorkerCapacity(profile(gpu_slot_count=2))
        cap.reserve_for_job(11, 'GPU')
        cap.assert_gpu_list_unchanged()  # the probe works once
        rebuild.request_rebuild()
        machine.fail_probe = True

        assert rebuild.step(cap) is None

        assert rebuild.pending() is True and rebuild._last_wait is None
        assert cap.profile.cards == fake_cards(2)
        machine.fail_probe = False
        assert rebuild.step(cap) == rebuild.SWAPPED

    def test_machine_whose_probe_never_works_rebuilds_from_blender(self, monkeypatch):
        machine = CardEnv(monkeypatch, count=2)
        machine.fail_probe = True
        cap = WorkerCapacity(profile(gpu_slot_count=2, gpu_mode='combined'))
        rebuild.request_rebuild()

        assert rebuild.step(cap) == rebuild.SWAPPED
        assert [card.key for card in cap.profile.cards] == [key(0), key(1)]
        assert cap.profile.gpu_mode == 'split'


class TestNoRebuildDuringUnreal:

    def test_exclusive_hold_skips_the_rebuild_and_its_probe(self, monkeypatch, mocker):
        machine = CardEnv(monkeypatch, count=1)
        cap = WorkerCapacity(profile(gpu_slot_count=1))
        cap.reserve_exclusive(5)
        rebuild.request_rebuild()
        build = mocker.spy(card_check, 'rebuild_profile')

        assert rebuild.step(cap) == rebuild.WAITING

        build.assert_not_called()
        assert machine.probes == 0 and rebuild.rebuild_waiting() is True
        cap.release_slot(5)
        assert rebuild.step(cap) == rebuild.SWAPPED and machine.probes == 1


class TestHandEditedTexts:

    def test_get_normalises_alert_and_missing_card_texts(self, monkeypatch, setup_complete):
        alert = {'id': 'ab' * 16, 'kind': 'card_lost', 'key': KEY_XT_23, 'name': ['not', 'text'],
                 'detail': 'line\nbreak ' + 'x' * 300, 'raised_at': 'y' * 100}
        record = {'key': 'pci:0000:41:00.0', 'name': 'Card\x1b[31m red\x00'}
        fake_saved(monkeypatch, {'revision': 1, 'method': 'gpu', 'cards': [record], 'alerts': [alert]})
        fake_probes(monkeypatch)

        status, answer = call(RUNTIME_GET)

        assert status == 200
        [shown] = answer['alerts']
        assert shown['name'] == "['not', 'text']"
        assert shown['detail'].startswith('line?break x') and len(shown['detail']) == 200
        assert shown['raised_at'] is None
        [missing] = answer['missing']
        assert missing['name'] == 'Card [31m red'
        assert all(ch.isprintable() for ch in missing['name'])
