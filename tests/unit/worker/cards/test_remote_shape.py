# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``cards.remote``: what happens before the checks (FR-CM-3, FR-CM-4, FR-CM-8, S2, AC-40).

- No request (absent, ``null``, ``{}``, an answer that is not an object): nothing is read or written.
- An id that is not 32 lower-case hex: ignored with one WARNING (until a valid id arrives), nothing reported.
- A malformed request is refused ``invalid`` naming the first bad field; its receipt is never logged, and a
  ``requested_by`` that is not ``user id=<pk>`` is never stored (S2).
- ``config.json`` unreadable, or an Unreal render holding the machine: the request waits; an exception never
  escapes ``handle_response``.
- The check probes the OS only: no screen read, no Blender run.
"""
import logging

import pytest

from sethlans_worker_agent import config_compute, config_store, unreal_bridge
from sethlans_worker_agent.cards import inventory, os_probe, remote, screens, store
from sethlans_worker_agent.cards.os_probe import OsCard
from tests.unit.worker._card_choice import AMD, KEY_XT_03, KEY_XT_23, XT_23_ID
from tests.unit.worker.cards._remote_helpers import (
    OUTCOME_KEYS, REQUEST_ID, Machine, answer, messages, request, ticked,
)

_MALFORMED = [
    ({'method': 'GPU'}, 'method'), ({'method': None}, 'method'), ({'gpu_mode': 'both'}, 'gpu_mode'),
    ({'cards': 'pci:0000:23:00.0'}, 'cards'), ({'cards': [ticked('0000:23:00.0')]}, 'cards'),
    ({'cards': [ticked('PCI:0000:23:00.0')]}, 'cards'), ({'cards': [ticked(), ticked()]}, 'cards'),
    ({'cards': [ticked(vendor_id=True)]}, 'cards'), ({'cards': [ticked(device_id=0x10000)]}, 'cards'),
    ({'cards': [{'key': KEY_XT_23, 'vendor_id': AMD}]}, 'cards'), ({'cards': [ticked()] * 17}, 'cards'),
    ({'shown': [KEY_XT_03]}, 'shown'), ({'shown': [KEY_XT_23, KEY_XT_23]}, 'shown'), ({'shown': None}, 'shown'),
    ({'base_revision': True}, 'base_revision'), ({'base_revision': -1}, 'base_revision'),
    ({'base_revision': 2 ** 31}, 'base_revision'), ({'base_revision': '0'}, 'base_revision'),
    ({'requested_by': 'admin'}, 'requested_by'), ({'requested_by': 'user id=1234567890123'}, 'requested_by'),
    ({'requested_by': 'user id=7\n'}, 'requested_by'),
    ({'requested_at': '2026-11-19T10:00:00'}, 'requested_at'), ({'requested_at': 'yesterday'}, 'requested_at'),
    ({'requested_at': 12}, 'requested_at'),
]


class TestNoRequest:

    @pytest.mark.parametrize('value', [None, 'absent', {}, []])
    def test_nothing_waiting_does_nothing(self, monkeypatch, caplog, value):
        machine = Machine(monkeypatch)
        reply = {'id': 3} if value == 'absent' else answer(value)
        remote.handle_response(reply)
        assert machine.writes == [] and machine.probes == 0 and messages(caplog) == []

    @pytest.mark.parametrize('reply', [None, [], 'text', 7])
    def test_answer_that_is_not_an_object_does_nothing(self, monkeypatch, reply):
        machine = Machine(monkeypatch)
        remote.handle_response(reply)
        assert machine.writes == []


class TestInvalidId:

    @pytest.mark.parametrize('request_id', [None, 'ab' * 15, 'AB' * 16, 'zz' * 16, 7, 'absent'])
    def test_ignored_with_one_warning_and_nothing_reported(self, monkeypatch, caplog, request_id):
        machine = Machine(monkeypatch)
        value = request(id=request_id)
        if request_id == 'absent':
            del value['id']
        remote.handle_response(answer(value))
        remote.handle_response(answer(value))

        assert machine.writes == [] and machine.probes == 0
        assert messages(caplog) == [remote.NO_ID]

    def test_warned_again_after_a_valid_request(self, monkeypatch, caplog):
        Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': False})
        remote.handle_response(answer(request(id='x')))
        remote.handle_response(answer(request()))
        remote.handle_response(answer(request(id='x')))
        assert messages(caplog, 'WARNING').count(remote.NO_ID) == 2


class TestShape:

    @pytest.mark.parametrize('change, field', _MALFORMED)
    def test_malformed_is_invalid_naming_the_field_without_a_receipt(self, monkeypatch, caplog, change, field):
        machine = Machine(monkeypatch)
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.cards.remote'):
            remote.handle_response(answer(request(**change)))

        reason = f'the request is malformed ({field})'
        assert machine.last_request['code'] == 'invalid' and machine.last_request['reason'] == reason
        assert machine.written_keys() == [OUTCOME_KEYS]
        assert machine.probes == 0 and machine.rebuilds == 0
        assert messages(caplog) == [f"Graphics card change '{REQUEST_ID}' refused ('invalid'): {ascii(reason)}."]

    def test_missing_field_is_invalid(self, monkeypatch):
        machine = Machine(monkeypatch)
        value = request()
        del value['base_revision']
        remote.handle_response(answer(value))
        assert machine.last_request['reason'] == 'the request is malformed (base_revision)'

    def test_a_username_is_never_stored(self, monkeypatch, caplog):
        machine = Machine(monkeypatch)
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.cards.remote'):
            remote.handle_response(answer(request(requested_by='alice', requested_at='soon')))
        assert machine.last_request['requested_by'] is None and machine.last_request['requested_at'] is None
        assert 'alice' not in str(machine.state) and all('alice' not in line for line in messages(caplog))

    def test_unknown_fields_are_ignored(self, monkeypatch):
        machine = Machine(monkeypatch)
        remote.handle_response(answer(request(extra={'deep': [1, 2]}, note='x')))
        assert machine.last_request['code'] == 'applied'

    def test_shape_is_checked_before_the_state(self, monkeypatch):
        machine = Machine(monkeypatch, {'revision': 0, 'allow_manager_changes': False})
        remote.handle_response(answer(request(base_revision=True)))
        assert machine.last_request['code'] == 'invalid'


class TestFailures:

    def test_unreadable_settings_leave_the_request_waiting(self, monkeypatch, caplog):
        machine = Machine(monkeypatch)

        def unreadable():
            raise config_store.ConfigReadError('locked')

        monkeypatch.setattr(config_compute, '_read_raw', unreadable)
        remote.handle_response(answer(request()))
        assert machine.writes == [] and machine.probes == 0
        assert remote.READ_FAILED in messages(caplog, 'WARNING')

    def test_waits_while_an_unreal_render_holds_the_machine(self, monkeypatch):
        machine = Machine(monkeypatch)
        monkeypatch.setattr(unreal_bridge, 'drift_check_allowed', lambda: False)
        remote.handle_response(answer(request()))
        assert machine.writes == [] and machine.probes == 0

        monkeypatch.setattr(unreal_bridge, 'drift_check_allowed', lambda: True)
        remote.handle_response(answer(request()))
        assert machine.last_request['code'] == 'applied' and machine.probes == 1

    def test_an_exception_never_escapes(self, monkeypatch, caplog):
        Machine(monkeypatch)

        def broken():
            raise RuntimeError('boom')

        monkeypatch.setattr(store, 'snapshot', broken)
        remote.handle_response(answer(request()))
        errors = [r for r in caplog.records if r.name == 'sethlans_worker_agent.cards.remote'
                  and r.levelname == 'ERROR']
        assert len(errors) == 1 and errors[0].exc_info is not None


class TestFreshInventory:

    def test_probes_the_os_only_and_joins_the_known_blender_list(self, monkeypatch):
        from sethlans_worker_agent.capacity import card_blender
        calls = []
        os_cards = (OsCard(key=KEY_XT_23, name='AMD Radeon RX 7900 XT', vendor_id=AMD, device_id=0x744C),)
        monkeypatch.setattr(os_probe, 'probe', lambda: calls.append('os') or os_cards)
        monkeypatch.setattr(card_blender, 'known', lambda: ([{'id': XT_23_ID, 'type': 'HIP'}], '5.2.2'))
        monkeypatch.setattr(screens, 'read', lambda *_a: pytest.fail('a manager request reads no screen marks'))
        real_build = inventory.build
        monkeypatch.setattr(inventory, 'build', lambda os_list, blender_list, version=None, **_kw:
                            real_build(os_list, blender_list, version, platform_name='linux'))

        inv = remote._fresh_inventory()

        assert calls == ['os'] and [card.key for card in inv.cards] == [KEY_XT_23]
        assert inv.cards[0].blender == (XT_23_ID, 'HIP')

    def test_failed_os_probe_is_none(self, monkeypatch):
        monkeypatch.setattr(os_probe, 'probe', lambda: None)
        assert remote._fresh_inventory() is None
