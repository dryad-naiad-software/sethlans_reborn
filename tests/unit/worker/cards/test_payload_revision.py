# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-MT-K-2: ``revision_id`` on every card of the ``cards`` report and of the
card GET answer (Unreal per-machine memory test, chunk 1).

- ``cards_view._card_view`` and ``_missing_view`` pass the id through as
  held (the inventory card, or the lost card's saved record).
- ``payload._card`` sends it when it is an integer in ``0..0xFFFF``, else
  ``null``: missing, ``bool``, string, negative or above ``0xFFFF``.
- A lost ticked card's revision comes from its saved record.

The saved section is a dict read through ``config_compute._read_raw``; the
probe is handed in through ``payload._probe_for_report`` (as in
``test_payload.py``).
"""
import dataclasses
import json

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import in_use, payload
from sethlans_worker_agent.web_ui import cards_view
from tests.unit.worker._card_choice import (
    KEY_RTX_A, KEY_XT_03, KEY_XT_23, XT_23_ID, inv, rtx_a, xt_03, xt_23,
)

HIP_LIST = [{'id': XT_23_ID, 'type': 'HIP'}]
INVALID = [0x10000, -1, True, False, 'cc', '0xcc', 204.0, [0xCC]]
INVALID_NAMES = ['over-ffff', 'negative', 'true', 'false', 'str', 'hex-str', 'float', 'list']


def saved(monkeypatch, *records):
    raw = {'method': 'gpu', 'gpu_mode': 'split', 'revision': 3, 'cards': [dict(r) for r in records],
           'known': [], 'alerts': []}
    monkeypatch.setattr(config_compute, '_read_raw', lambda: json.loads(json.dumps(raw)))


def probe(monkeypatch, inventory):
    monkeypatch.setattr(payload, '_probe_for_report', lambda: cards_view.Probe(inventory, HIP_LIST))


def revisions(report):
    return {entry['key']: entry['revision_id'] for entry in report['cards']}


def _answer(cards=(), missing=()):
    """A card GET answer holding only the given card and missing entries."""
    return {'method': 'gpu', 'gpu_mode': 'split', 'revision': 1, 'rebuild_waiting': False, 'problem': '',
            'cards': list(cards), 'missing': list(missing), 'alerts': [], 'notices': [], 'warnings': []}


def _entry(revision, key=KEY_XT_23, **fields):
    entry = {'key': key, 'name': 'AMD Radeon RX 7900 XT', 'vendor_id': 0x1002, 'device_id': 0x744C,
             'revision_id': revision, 'ticked': True, 'state': 'ok', **fields}
    return entry


def _shape(**answer):
    return payload.shape(_answer(**answer), config_compute.parse({}, {}), os_probe_failed=False)


class TestPayloadFromTheInventory:

    def test_present_revisions_are_sent(self, monkeypatch):
        saved(monkeypatch, in_use.record_of(xt_23()))
        probe(monkeypatch, inv(xt_03(), xt_23(), rtx_a()))
        assert revisions(payload.build()) == {KEY_RTX_A: 0xA1, KEY_XT_03: 0xC8, KEY_XT_23: 0xCC}

    def test_missing_revision_is_null(self, monkeypatch):
        saved(monkeypatch, in_use.record_of(xt_23(revision_id=None)))
        probe(monkeypatch, inv(xt_03(revision_id=None), xt_23(revision_id=None)))
        report = payload.build()
        assert revisions(report) == {KEY_XT_03: None, KEY_XT_23: None}
        assert all('revision_id' in entry for entry in report['cards'])

    def test_zero_revision_is_sent_as_zero(self, monkeypatch):
        saved(monkeypatch)
        probe(monkeypatch, inv(xt_23(revision_id=0)))
        assert revisions(payload.build()) == {KEY_XT_23: 0}

    def test_largest_revision_is_sent(self, monkeypatch):
        saved(monkeypatch)
        probe(monkeypatch, inv(xt_23(revision_id=0xFFFF)))
        assert revisions(payload.build()) == {KEY_XT_23: 0xFFFF}

    @pytest.mark.parametrize('revision', INVALID, ids=INVALID_NAMES)
    def test_invalid_inventory_revision_is_null(self, monkeypatch, revision):
        saved(monkeypatch)
        probe(monkeypatch, inv(dataclasses.replace(xt_23(), revision_id=revision)))
        report = payload.build()
        assert revisions(report) == {KEY_XT_23: None}
        assert json.loads(json.dumps(report)) == report

    def test_invalid_revision_keeps_the_card_and_its_other_ids(self, monkeypatch):
        saved(monkeypatch)
        probe(monkeypatch, inv(dataclasses.replace(xt_23(), revision_id=0x10000)))
        [entry] = payload.build()['cards']
        assert (entry['key'], entry['vendor_id'], entry['device_id'], entry['revision_id']) == (
            KEY_XT_23, 0x1002, 0x744C, None)


class TestPayloadOfALostCard:

    def test_lost_card_revision_comes_from_its_saved_record(self, monkeypatch):
        saved(monkeypatch, in_use.record_of(rtx_a()), in_use.record_of(xt_23()))
        probe(monkeypatch, inv(xt_23()))
        report = payload.build()
        assert revisions(report) == {KEY_RTX_A: 0xA1, KEY_XT_23: 0xCC}
        assert {e['key']: e['state'] for e in report['cards']}[KEY_RTX_A] == 'lost'

    def test_lost_card_saved_without_a_revision_is_null(self, monkeypatch):
        saved(monkeypatch, in_use.record_of(dataclasses.replace(rtx_a(), revision_id=None)))
        probe(monkeypatch, inv(xt_23()))
        assert revisions(payload.build())[KEY_RTX_A] is None

    def test_lost_card_record_without_the_revision_key_is_null(self, monkeypatch):
        record = in_use.record_of(rtx_a())
        del record['revision_id']
        saved(monkeypatch, record)
        probe(monkeypatch, inv(xt_23()))
        assert revisions(payload.build())[KEY_RTX_A] is None

    def test_lost_card_saved_with_revision_zero_is_zero(self, monkeypatch):
        saved(monkeypatch, in_use.record_of(dataclasses.replace(rtx_a(), revision_id=0)))
        probe(monkeypatch, inv(xt_23()))
        assert revisions(payload.build())[KEY_RTX_A] == 0


class TestCardShape:
    """``payload._card`` through ``payload.shape`` on a hand-made GET answer."""

    @pytest.mark.parametrize('revision', [0, 0xCC, 0xFF, 0x100, 0xFFFF])
    def test_valid_revision_is_sent(self, revision):
        [entry] = _shape(cards=[_entry(revision)])['cards']
        assert entry['revision_id'] == revision

    @pytest.mark.parametrize('revision', [None] + INVALID, ids=['none'] + INVALID_NAMES)
    def test_invalid_card_revision_is_null(self, revision):
        [entry] = _shape(cards=[_entry(revision)])['cards']
        assert entry['revision_id'] is None
        assert entry['state'] == 'ok'

    def test_absent_card_revision_is_null(self):
        entry = _entry(0xCC)
        del entry['revision_id']
        [sent] = _shape(cards=[entry])['cards']
        assert 'revision_id' in sent and sent['revision_id'] is None

    @pytest.mark.parametrize('revision', [None] + INVALID, ids=['none'] + INVALID_NAMES)
    def test_invalid_missing_card_revision_is_null(self, revision):
        missing = {'key': KEY_RTX_A, 'name': 'NVIDIA GeForce RTX 4080', 'vendor_id': 0x10DE, 'device_id': 0x2704,
                   'revision_id': revision, 'state': 'lost'}
        [entry] = _shape(missing=[missing])['cards']
        assert (entry['ticked'], entry['state'], entry['revision_id']) == (True, 'lost', None)

    def test_revision_sits_after_the_device_id(self):
        [entry] = _shape(cards=[_entry(0xCC)])['cards']
        keys = list(entry)
        assert keys[keys.index('device_id') + 1] == 'revision_id'


class TestCardsView:
    """The card GET answer passes the id through as held; the payload checks it."""

    def test_card_view_carries_the_inventory_revision(self):
        answer = cards_view.view(cards_view.Probe(inv(xt_03(), xt_23(revision_id=None)), []),
                                 config_compute.ComputeSection())
        assert [(c['key'], c['revision_id']) for c in answer['cards']] == [(KEY_XT_03, 0xC8), (KEY_XT_23, None)]

    def test_missing_view_carries_the_saved_revision(self):
        record = in_use.record_of(rtx_a())
        assert cards_view._missing_view(record, 'lost')['revision_id'] == 0xA1

    def test_missing_view_without_a_saved_revision_is_none(self):
        record = in_use.record_of(rtx_a())
        del record['revision_id']
        view = cards_view._missing_view(record, 'unknown')
        assert (view['state'], view['revision_id'], view['vendor_id'], view['device_id']) == (
            'unknown', None, 0x10DE, 0x2704)

    def test_lost_card_in_the_answer_has_its_saved_revision(self):
        sect = config_compute.parse({'method': 'gpu', 'gpu_mode': 'split', 'revision': 1,
                                     'cards': [in_use.record_of(rtx_a())]}, {})
        answer = cards_view.view(cards_view.Probe(inv(xt_23()), []), sect)
        assert [(m['key'], m['revision_id']) for m in answer['missing']] == [(KEY_RTX_A, 0xA1)]
