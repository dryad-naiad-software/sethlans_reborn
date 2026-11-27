# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the alert writers of ``cards.store`` (FR-CC-4, FR-CK-5, C5, C-20, C-21, AC-42).

The saved ``compute`` section lives in a dict that ``config_compute._read_raw``
reads; a fake ``config_store.set_many`` applies each write, or raises when
the test makes the file unwritable.
"""
import copy
import logging
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.cards import store
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError
from tests.unit.worker._card_choice import KEY_XT_03, KEY_XT_23, inv, xt_03, xt_23


@pytest.fixture
def saved(monkeypatch):
    state = {'compute': {'revision': 0}, 'fail': None, 'read_fails': False}
    writes = []

    def read_raw():
        if state['read_fails']:
            raise config_store.ConfigReadError('could not read config.json: busy')
        return copy.deepcopy(state['compute'])

    def set_many(pairs, expect=None):
        pairs = list(pairs)
        writes.append(SimpleNamespace(pairs=pairs, locked=store._section_lock.locked()))
        if state['fail'] is not None:
            raise state['fail']
        for dotted, value in pairs:
            state['compute'][dotted.split('.')[1]] = copy.deepcopy(value)

    monkeypatch.setattr(config_compute, '_read_raw', read_raw)
    monkeypatch.setattr(config_store, 'set_many', set_many)
    return SimpleNamespace(state=state, writes=writes)


def _saved_alerts(saved):
    return saved.state['compute'].get('alerts', [])


class TestAddAlert:

    def test_new_alert_record(self, saved):
        assert store.add_alert('card_lost', KEY_XT_03, 'AMD Radeon RX 7900 XT', 'missing from the card list') is True

        [alert] = _saved_alerts(saved)
        assert re.fullmatch(r'[0-9a-f]{32}', alert['id'])
        assert (alert['kind'], alert['key'], alert['name'], alert['detail']) == (
            'card_lost', KEY_XT_03, 'AMD Radeon RX 7900 XT', 'missing from the card list')
        assert datetime.fromisoformat(alert['raised_at']).utcoffset().total_seconds() == 0
        assert len(saved.writes) == 1 and saved.writes[0].locked is True
        assert store.snapshot().alerts == (alert,)  # valid for config_compute: the method is not reset
        assert store.snapshot().invalid == ()

    def test_repeat_of_kind_and_key_is_not_added(self, saved):
        assert store.add_alert('card_lost', KEY_XT_03, 'x', 'd') is True
        assert store.add_alert('card_lost', KEY_XT_03, 'x', 'd') is False
        assert len(_saved_alerts(saved)) == 1 and len(saved.writes) == 1

    def test_other_kind_or_key_is_added(self, saved):
        store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        assert store.add_alert('card_changed', KEY_XT_03, 'x', 'd') is True
        assert store.add_alert('card_lost', KEY_XT_23, 'x', 'd') is True
        assert len(_saved_alerts(saved)) == 3

    def test_detail_is_printable_ascii_and_capped(self, saved):
        store.add_alert('card_changed', KEY_XT_03, 'x', 'was Rädeon\n' + 'y' * 300)
        detail = _saved_alerts(saved)[0]['detail']
        assert len(detail) == 200 and detail.startswith('was R?deon?y')

    def test_cap_drops_the_oldest_with_a_warning(self, saved, caplog):
        caplog.set_level(logging.WARNING, logger='sethlans_worker_agent.cards.store')
        keys = [f'pci:0000:{bus:02x}:00.0' for bus in range(1, 18)]
        for key in keys:
            store.add_alert('card_lost', key, 'x', 'd')
        assert [alert['key'] for alert in _saved_alerts(saved)] == keys[1:]
        assert any('oldest' in record.getMessage() for record in caplog.records)

    @pytest.mark.parametrize('kind, key', [('card_gone', KEY_XT_03), ('card_lost', '0000:03:00.0')])
    def test_not_an_alert(self, saved, kind, key):
        with pytest.raises(ValueError):
            store.add_alert(kind, key, 'x', 'd')
        assert saved.writes == []


class TestFailedWrite:
    """C5, C-21, AC-42: a failed write keeps the alert in memory and raises."""

    @pytest.mark.parametrize('error', [ConfigLockTimeoutError('lock'), OSError('disk full')])
    def test_alert_kept_in_memory_and_merged_into_reads(self, saved, error):
        saved.state['fail'] = error
        with pytest.raises(type(error)):
            store.add_alert('card_lost', KEY_XT_03, 'x', 'missing')

        assert _saved_alerts(saved) == []
        assert [(a['kind'], a['key']) for a in store.snapshot().alerts] == [('card_lost', KEY_XT_03)]

    def test_repeat_while_in_memory_is_not_added_again(self, saved):
        saved.state['fail'] = OSError('disk full')
        with pytest.raises(OSError):
            store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        assert store.add_alert('card_lost', KEY_XT_03, 'x', 'd') is False  # its retry fails quietly
        assert len(store.snapshot().alerts) == 1

    def test_unreadable_file_keeps_the_alert_in_memory(self, saved):
        saved.state['read_fails'] = True
        with pytest.raises(config_store.ConfigReadError):
            store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        assert saved.writes == []
        saved.state['read_fails'] = False
        assert [a['key'] for a in store.snapshot().alerts] == [KEY_XT_03]

    def test_held_check_of_save_sees_the_alert_in_memory(self, saved):
        """C-21: ``save`` reads through the same merge as ``snapshot``."""
        saved.state['fail'] = OSError('disk full')
        with pytest.raises(OSError):
            store.add_alert('card_lost', KEY_XT_23, 'x', 'd')
        saved.state['fail'] = None
        with pytest.raises(store.CardHeld):
            store.save('gpu', 'split', [KEY_XT_23], [KEY_XT_23], inv(xt_03(), xt_23()), 0)

    def test_next_successful_write_persists_and_clears_memory(self, saved):
        saved.state['fail'] = OSError('disk full')
        with pytest.raises(OSError):
            store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        saved.state['fail'] = None

        store.save('cpu', 'split', [], [], inv(xt_03()), 0)

        assert [a['key'] for a in _saved_alerts(saved)] == [KEY_XT_03]
        assert store._memory_alerts == []
        assert len(store.snapshot().alerts) == 1

    def test_repeat_after_the_file_recovers_writes_the_memory(self, saved):
        saved.state['fail'] = OSError('disk full')
        with pytest.raises(OSError):
            store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        saved.state['fail'] = None
        assert store.add_alert('card_lost', KEY_XT_03, 'x', 'd') is False
        assert [a['key'] for a in _saved_alerts(saved)] == [KEY_XT_03] and store._memory_alerts == []

    def test_another_alert_after_recovery_writes_both(self, saved):
        saved.state['fail'] = OSError('disk full')
        with pytest.raises(OSError):
            store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        saved.state['fail'] = None
        assert store.add_alert('card_lost', KEY_XT_23, 'x', 'd') is True
        assert [a['key'] for a in _saved_alerts(saved)] == [KEY_XT_03, KEY_XT_23]


class TestRemoveAlerts:

    def test_removes_named_ids_only(self, saved):
        store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        store.add_alert('card_lost', KEY_XT_23, 'y', 'd')
        first = _saved_alerts(saved)[0]['id']

        removed = store.remove_alerts([first, 'f' * 32])

        assert [a['id'] for a in removed] == [first]
        assert [a['key'] for a in _saved_alerts(saved)] == [KEY_XT_23]

    def test_unknown_id_writes_nothing(self, saved):
        assert store.remove_alerts(['f' * 32]) == []
        assert saved.writes == []

    def test_removing_an_alert_kept_in_memory(self, saved):
        saved.state['fail'] = OSError('disk full')
        with pytest.raises(OSError):
            store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        saved.state['fail'] = None
        [alert] = store.snapshot().alerts

        assert [a['id'] for a in store.remove_alerts([alert['id']])] == [alert['id']]
        assert store.snapshot().alerts == () and store._memory_alerts == []

    def test_failed_write_removes_nothing_from_memory(self, saved):
        """The dismiss answers 503; the alert must still be open, not silently gone."""
        saved.state['fail'] = OSError('disk full')
        with pytest.raises(OSError):
            store.add_alert('card_lost', KEY_XT_03, 'x', 'd')
        [alert] = store.snapshot().alerts
        with pytest.raises(OSError):
            store.remove_alerts([alert['id']])
        assert store.snapshot().alerts == (alert,)

        saved.state['fail'] = None
        assert [a['id'] for a in store.remove_alerts([alert['id']])] == [alert['id']]
        assert store.snapshot().alerts == () and store._memory_alerts == []


class TestStrictRead:
    """C-20: writers never act on defaults from an unreadable file."""

    def test_save_is_refused(self, saved):
        saved.state['compute'] = {'revision': 4, 'alerts': []}
        saved.state['read_fails'] = True
        with pytest.raises(config_store.ConfigReadError):
            store.save('cpu', 'split', [], [], inv(xt_03()), 0)
        assert saved.writes == []

    def test_remove_is_refused(self, saved):
        saved.state['read_fails'] = True
        with pytest.raises(config_store.ConfigReadError):
            store.remove_alerts(['f' * 32])
        assert saved.writes == []

    def test_snapshot_reports_the_failed_read(self, saved):
        saved.state['compute'] = {'method': 'gpu', 'revision': 4}
        saved.state['read_fails'] = True
        section = store.snapshot()
        assert section.read_failed is True and (section.method, section.revision) == ('cpu', 0)
        saved.state['read_fails'] = False
        assert store.snapshot().read_failed is False
