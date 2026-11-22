# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-CR-1: the ``cards`` object of registration and every heartbeat (``cards.payload``).

- The shape, with one card in each state (``ok``, ``unticked``, ``lost``,
  ``changed``, ``held``, ``blender_lost``, ``unknown``); a lost ticked card
  is a card entry built from its saved record.
- ``manager_changes_allowed``: the saved switch, false under env override.
- What is sent when ``config.json`` cannot be read (nothing) or the OS
  probe failed (the Blender cards plus the ``os_probe_failed`` warning).
- ``add_to`` never raises.

The saved section is a dict read through ``config_compute._read_raw``; the
probe is handed in through ``payload._probe_for_report``.
"""
import dataclasses
import json

import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.cards import in_use, payload
from sethlans_worker_agent.cards.inventory import Inventory
from sethlans_worker_agent.web_ui import cards_view
from tests.unit.worker._card_choice import (
    AMD, KEY_RTX_A, KEY_XT_03, KEY_XT_23, XT_23_ID, card, inv, rtx_a, xt_03, xt_23,
)

HIP_LIST = [{'id': XT_23_ID, 'type': 'HIP'}]
RAISED = '2026-11-19T08:00:00+00:00'


def alert(kind, key, letter='a', name='AMD Radeon RX 7900 XT', detail='missing from the card list'):
    return {'id': letter * 32, 'kind': kind, 'key': key, 'name': name, 'detail': detail, 'raised_at': RAISED}


def saved(monkeypatch, *records, alerts=(), method='gpu', known=(), **extra):
    raw = {'method': method, 'gpu_mode': 'split', 'revision': 3, 'cards': [dict(r) for r in records],
           'known': list(known), 'alerts': [dict(a) for a in alerts], **extra}
    monkeypatch.setattr(config_compute, '_read_raw', lambda: json.loads(json.dumps(raw)))


def probe(monkeypatch, inventory, blender_cards=HIP_LIST):
    monkeypatch.setattr(payload, '_probe_for_report', lambda: cards_view.Probe(inventory, blender_cards))


def by_key(report):
    return {entry['key']: entry for entry in report['cards']}


class TestShape:

    def test_one_ok_and_one_unticked_card_exactly(self, monkeypatch):
        saved(monkeypatch, in_use.record_of(xt_23()), known=[KEY_XT_03, KEY_XT_23])
        probe(monkeypatch, inv(xt_03(), xt_23()))

        report = payload.build()

        assert report == {
            'schema': 1, 'method': 'gpu', 'gpu_mode': 'split', 'env_override': False, 'revision': 3,
            'manager_changes_allowed': True, 'rebuild_waiting': False, 'problem': '',
            'cards': [
                {'key': KEY_XT_03, 'name': 'AMD Radeon RX 7900 XT', 'vendor_id': AMD, 'device_id': 0x744C,
                 'revision_id': 0xC8, 'ticked': False, 'state': 'unticked', 'blender': True, 'backend': 'HIP', 'note': '',
                 'screen': None, 'primary_screen': None, 'job_id': None, 'renderer': None},
                {'key': KEY_XT_23, 'name': 'AMD Radeon RX 7900 XT', 'vendor_id': AMD, 'device_id': 0x744C,
                 'revision_id': 0xCC, 'ticked': True, 'state': 'ok', 'blender': True, 'backend': 'HIP', 'note': '',
                 'screen': None, 'primary_screen': None, 'job_id': None, 'renderer': None},
            ],
            'alerts': [], 'notices': [],
            'warnings': [{'code': 'engine_not_tied', 'key': None, 'text': payload.text(
                'Eevee and Workbench jobs may render on any graphics card of this machine, including unticked '
                'ones. The ticks are firm for Cycles and Unreal only until issue #251 is fixed.', 200)}],
            'request_outcome': None,
        }
        assert json.loads(json.dumps(report)) == report

    def test_lost_ticked_card_is_an_entry_from_its_saved_record(self, monkeypatch):
        saved(monkeypatch, in_use.record_of(rtx_a()), in_use.record_of(xt_23()),
              alerts=[alert('card_lost', KEY_RTX_A, name='NVIDIA GeForce RTX 4080')])
        probe(monkeypatch, inv(xt_23()))

        lost = by_key(payload.build())[KEY_RTX_A]

        assert lost == {'key': KEY_RTX_A, 'name': 'NVIDIA GeForce RTX 4080', 'vendor_id': 0x10DE,
                        'device_id': 0x2704, 'revision_id': 0xA1, 'ticked': True, 'state': 'lost', 'blender': False,
                        'backend': None,
                        'note': 'missing from the card list', 'screen': None, 'primary_screen': None,
                        'job_id': None, 'renderer': None}

    def test_changed_held_and_blender_lost(self, monkeypatch):
        other_model = card(KEY_XT_03, 'AMD Radeon RX 7800 XT', vendor_id=AMD, device_id=0x747E)
        saved(monkeypatch, in_use.record_of(other_model), in_use.record_of(xt_23()), in_use.record_of(rtx_a()),
              alerts=[alert('card_lost', KEY_XT_23), alert('card_lost_blender', KEY_RTX_A, 'b')])
        probe(monkeypatch, inv(rtx_a(), xt_03(), xt_23()))

        report = payload.build()

        states = {key: entry['state'] for key, entry in by_key(report).items()}
        assert states == {KEY_XT_03: 'changed', KEY_XT_23: 'held', KEY_RTX_A: 'blender_lost'}
        assert by_key(report)[KEY_RTX_A]['backend'] == 'OPTIX'
        assert report['problem'].startswith('None of the ticked graphics cards can be used')
        assert [(a['kind'], a['key'], a['id']) for a in report['alerts']] == [
            ('card_lost', KEY_XT_23, 'a' * 32), ('card_lost_blender', KEY_RTX_A, 'b' * 32)]
        assert report['alerts'][0] == alert('card_lost', KEY_XT_23)

    def test_unknown_after_a_failed_os_probe_with_a_warning(self, monkeypatch):
        saved(monkeypatch, in_use.record_of(rtx_a()), in_use.record_of(xt_23()))
        fallback = Inventory(cards=(card(KEY_XT_23, 'AMD Radeon RX 7900 XT', XT_23_ID, 'HIP'),),
                             os_probe_failed=True, blender_version='4.5.0')
        probe(monkeypatch, fallback)

        report = payload.build()

        cards = by_key(report)
        assert cards[KEY_RTX_A]['state'] == 'unknown'
        assert cards[KEY_RTX_A]['note'] == 'the operating system could not list the graphics cards'
        assert (cards[KEY_XT_23]['vendor_id'], cards[KEY_XT_23]['device_id']) == (None, None)
        assert report['warnings'][0] == {'code': 'os_probe_failed', 'key': None, 'text': payload.OS_PROBE_TEXT}

    def test_note_backend_and_screen_marks_pass_through(self, monkeypatch):
        saved(monkeypatch, method='cpu')
        marked = card(KEY_XT_03, 'AMD Radeon RX 7900 XT', vendor_id=AMD, device_id=0x744C,
                      note='not offered by Blender 4.5.0')
        marked = dataclasses.replace(marked, screen=True, primary_screen=False)
        probe(monkeypatch, inv(marked))

        [entry] = payload.build()['cards']

        assert (entry['blender'], entry['backend'], entry['note']) == (False, None, 'not offered by Blender 4.5.0')
        assert (entry['screen'], entry['primary_screen']) == (True, False)

    def test_unknown_blender_backend_is_sent_as_null(self, monkeypatch):
        saved(monkeypatch, method='cpu')
        probe(monkeypatch, inv(card(KEY_XT_03, 'Card', 'VULKAN_x_0000:03:00', 'VULKAN')))
        [entry] = payload.build()['cards']
        assert (entry['blender'], entry['backend']) == (True, None)

    def test_notices_are_sent(self, monkeypatch):
        saved(monkeypatch, method='cpu', known=[KEY_RTX_A])
        probe(monkeypatch, inv(xt_03()))
        assert payload.build()['notices'] == [
            {'kind': 'card_new', 'key': KEY_XT_03, 'name': 'AMD Radeon RX 7900 XT'},
            {'kind': 'card_gone', 'key': KEY_RTX_A, 'name': KEY_RTX_A}]


class TestManagerChangesAllowed:

    def test_saved_switch_off(self, monkeypatch):
        saved(monkeypatch, allow_manager_changes=False)
        probe(monkeypatch, inv())
        assert payload.build()['manager_changes_allowed'] is False

    def test_default_is_true(self, monkeypatch):
        saved(monkeypatch)
        probe(monkeypatch, inv())
        assert payload.build()['manager_changes_allowed'] is True

    def test_env_override_reports_false(self, monkeypatch):
        saved(monkeypatch)
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        probe(monkeypatch, inv())
        report = payload.build()
        assert (report['env_override'], report['manager_changes_allowed']) == (True, False)


class TestFailures:

    def test_unreadable_config_sends_no_report(self, monkeypatch):
        def unreadable():
            raise config_store.ConfigReadError('busy')
        monkeypatch.setattr(config_compute, '_read_raw', unreadable)
        monkeypatch.setattr(payload, '_probe_for_report', lambda: pytest.fail('no probe for an unreadable file'))
        heartbeat = {'status': 'IDLE'}

        payload.add_to(heartbeat)

        assert heartbeat == {'status': 'IDLE'}
        assert payload.build() is None

    def test_add_to_never_raises(self, monkeypatch, caplog):
        saved(monkeypatch)

        def broken():
            raise RuntimeError('probe exploded')
        monkeypatch.setattr(payload, '_probe_for_report', broken)
        heartbeat = {}

        payload.add_to(heartbeat)
        payload.add_to(heartbeat)

        assert 'cards' not in heartbeat
        errors = [r for r in caplog.records if r.name == payload.logger.name and r.levelname == 'ERROR']
        assert len(errors) == 1

    def test_add_to_puts_the_report_under_cards(self, monkeypatch):
        saved(monkeypatch)
        probe(monkeypatch, inv(xt_23()))
        heartbeat = {}
        payload.add_to(heartbeat)
        assert heartbeat['cards']['schema'] == 1
