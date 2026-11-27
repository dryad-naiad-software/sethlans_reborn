# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``cards.remote`` and ``cards.store`` against the chunk 10 concurrency review (FR-CM-1, FR-CM-5, FR-CM-7, D16).

1. The switch is re-checked under the section lock: turned off during the request's probe, the change is
   refused ``not_allowed`` and nothing is saved (``store.save(manager_request=True)`` -> ``ManagerChangesOff``).
2. The outcome kept in memory is emptied by the write of a later outcome, in the same critical section, so it
   never outlives that write, also when something fails after the save (here ``request_rebuild``).
3. An older id delivered late is not evaluated again; the saved list of evaluated ids holds at most 8.
4. ``config.json`` unreadable inside the save: no outcome, the next heartbeat retries.
"""
import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import remote, store, store_requests
from tests.unit.worker._card_choice import inv, xt_03, xt_23
from tests.unit.worker.cards._remote_helpers import REQUEST_ID, Machine, answer, request

OTHER_ID = 'cd' * 16


def _fail_writes(monkeypatch):
    def fail(_pairs, expect=None):
        raise OSError('read-only')
    monkeypatch.setattr(config_store, 'set_many', fail)


class TestSwitchUnderTheLock:

    def test_switch_turned_off_during_the_probe_is_not_allowed(self, monkeypatch):
        machine = Machine(monkeypatch)

        def probe_while_the_person_turns_it_off():
            machine.probes += 1
            machine.compute['allow_manager_changes'] = False
            return inv(xt_03(), xt_23())

        monkeypatch.setattr(remote, '_fresh_inventory', probe_while_the_person_turns_it_off)
        remote.handle_response(answer(request()))

        assert machine.last_request['code'] == 'not_allowed' and machine.probes == 1
        assert 'cards' not in machine.compute and machine.compute['revision'] == 0
        assert machine.rebuilds == 0

    def test_page_saves_ignore_the_switch(self, monkeypatch):
        Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': False})
        assert store.save('gpu', 'split', ['pci:0000:23:00.0'], ['pci:0000:23:00.0'], inv(xt_23()), 0) == 1

    def test_manager_save_refused_when_the_switch_is_off(self, monkeypatch):
        Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': False})
        with pytest.raises(store.ManagerChangesOff):
            store.save('gpu', 'split', ['pci:0000:23:00.0'], ['pci:0000:23:00.0'], inv(xt_23()), 0,
                       manager_request=True)


class TestMemoryEmptiedByTheNextWrite:

    def _refused_in_memory(self, monkeypatch, machine):
        real_set_many = config_store.set_many
        _fail_writes(monkeypatch)
        machine.compute['allow_manager_changes'] = False
        remote.handle_response(answer(request(id=OTHER_ID)))
        assert store_requests.last_request(store.snapshot())['code'] == 'not_allowed'
        monkeypatch.setattr(config_store, 'set_many', real_set_many)
        machine.compute['allow_manager_changes'] = True

    def test_applied_outcome_replaces_the_one_in_memory(self, monkeypatch):
        machine = Machine(monkeypatch)
        self._refused_in_memory(monkeypatch, machine)
        remote.handle_response(answer(request()))

        assert store.remembered_request() == (None, [])
        assert store_requests.view_fields(store.snapshot())['last_request']['code'] == 'applied'
        assert machine.compute['recent_request_ids'] == [OTHER_ID, REQUEST_ID]

    def test_failure_after_the_save_cannot_leave_the_old_memory(self, monkeypatch):
        machine = Machine(monkeypatch)
        self._refused_in_memory(monkeypatch, machine)

        def broken():
            raise RuntimeError('rebuild failed')

        monkeypatch.setattr(rebuild, 'request_rebuild', broken)
        remote.handle_response(answer(request()))  # logged, never raised

        assert machine.compute['revision'] == 1
        assert store_requests.last_request(store.snapshot())['code'] == 'applied'


class TestOlderIds:

    def test_a_late_older_id_is_not_evaluated_again(self, monkeypatch):
        machine = Machine(monkeypatch)
        remote.handle_response(answer(request(id=OTHER_ID)))
        remote.handle_response(answer(request(base_revision=1)))
        writes = len(machine.writes)
        remote.handle_response(answer(request(id=OTHER_ID)))  # re-ordered delivery of the first one

        assert len(machine.writes) == writes and machine.probes == 2
        assert machine.last_request['id'] == REQUEST_ID

    def test_ids_kept_in_memory_also_count(self, monkeypatch):
        machine = Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': False})
        _fail_writes(monkeypatch)
        remote.handle_response(answer(request(id=OTHER_ID)))
        remote.handle_response(answer(request()))
        assert store.remembered_request()[1] == [OTHER_ID, REQUEST_ID]
        remote.handle_response(answer(request(id=OTHER_ID)))
        assert store_requests.last_request(store.snapshot())['id'] == REQUEST_ID
        assert machine.writes == []

    def test_the_list_holds_the_last_eight(self, monkeypatch):
        machine = Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': False})
        ids = [f'{n:032x}' for n in range(1, 11)]
        for request_id in ids:
            remote.handle_response(answer(request(id=request_id)))

        assert machine.compute['recent_request_ids'] == ids[-8:]
        writes = len(machine.writes)
        remote.handle_response(answer(request(id=ids[0])))  # fell out of the list: evaluated again
        assert len(machine.writes) == writes + 1

    def test_a_saved_list_that_is_not_a_list_is_ignored(self, monkeypatch):
        section = config_compute.parse({'recent_request_ids': 'x', 'method': 'gpu', 'cards': []}, {})
        assert section.recent_request_ids == () and 'compute.recent_request_ids' not in section.invalid


class TestUnreadableInsideTheSave:

    def test_no_outcome_and_retried_next_heartbeat(self, monkeypatch):
        machine = Machine(monkeypatch)
        readable = config_compute._read_raw

        def unreadable():
            raise config_store.ConfigReadError('locked during os.replace')

        def probe_then_lose_the_file():
            machine.probes += 1
            monkeypatch.setattr(config_compute, '_read_raw', unreadable)
            return inv(xt_03(), xt_23())

        monkeypatch.setattr(remote, '_fresh_inventory', probe_then_lose_the_file)
        remote.handle_response(answer(request()))
        assert machine.writes == [] and store.remembered_request() == (None, [])

        monkeypatch.setattr(config_compute, '_read_raw', readable)
        monkeypatch.setattr(remote, '_fresh_inventory', lambda: inv(xt_03(), xt_23()))
        remote.handle_response(answer(request()))
        assert machine.last_request['code'] == 'applied'
