# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``views.heartbeat_cards`` (worker card selection FR-CR-3,
AC-15): a valid report is stored, every invalid entry is dropped and the
rest kept, unknown keys are dropped, strings are capped and sanitised.
"""

from types import SimpleNamespace

import pytest

from workers.card_keys import is_card_key
from workers.views import heartbeat_cards
from workers.views.heartbeat_cards import apply_card_report, is_alert_id, validate_card_report

from .card_report_helpers import (
    ALERT_ID_2, KEY, KEY_2, alert, card, notice, report, stored, warning,
)


def _kept(name, *entries):
    """The entries ``validate_card_report`` keeps of list ``name``."""
    return validate_card_report(report(**{name: list(entries)}))[name]


class TestWholeReport:

    def test_valid_report_is_stored_without_request_outcome(self):
        assert validate_card_report(report()) == stored()

    @pytest.mark.parametrize('raw', [None, 'cards', [], 1, {'cards': []}])
    def test_not_an_object_with_schema_is_unusable(self, raw):
        assert validate_card_report(raw) is None

    @pytest.mark.parametrize('schema', [2, 0, '1', True, 1.0, None])
    def test_schema_must_be_the_integer_one(self, schema):
        assert validate_card_report(report(schema=schema)) is None

    def test_unknown_keys_are_dropped_at_every_level(self):
        raw = report(future=1, cards=[card(future=2)], alerts=[alert(future=3)],
                     notices=[notice(future=4)], warnings=[warning(future=5)])
        assert validate_card_report(raw) == stored()

    def test_missing_lists_are_empty(self):
        result = validate_card_report({'schema': 1})
        assert result == {'schema': 1, 'cards': [], 'alerts': [], 'notices': [], 'warnings': []}

    @pytest.mark.parametrize('name', ['cards', 'alerts', 'notices', 'warnings'])
    def test_a_list_that_is_not_a_list_is_empty(self, name):
        assert validate_card_report(report(**{name: {'0': card()}}))[name] == []

    @pytest.mark.parametrize('field, value', [
        ('method', 'all'), ('gpu_mode', 'mixed'), ('env_override', 1),
        ('revision', -1), ('revision', 2 ** 31), ('revision', True), ('revision', '3'),
        ('manager_changes_allowed', 'yes'), ('rebuild_waiting', None), ('problem', 7),
    ])
    def test_invalid_top_level_value_is_left_out(self, field, value):
        result = validate_card_report(report(**{field: value}))
        assert field not in result
        assert result['cards'] == [card()]

    def test_problem_is_capped_and_sanitised(self):
        result = validate_card_report(report(problem='<b>' + 'x' * 300))
        assert result['problem'] == '?b?' + 'x' * 197


class TestCards:

    @pytest.mark.parametrize('field, value', [
        ('key', 'pci:0000:01:00'), ('key', 'PCI:0000:01:00.0'), ('key', 'pci:0000:01:00.8'),
        ('key', '0000:01:00.0'), ('key', None), ('state', 'gone'), ('state', None),
        ('vendor_id', -1), ('vendor_id', 65536), ('vendor_id', True), ('device_id', '0x2b85'),
        ('ticked', 1), ('blender', 'true'), ('backend', 'VULKAN'), ('backend', 'optix'),
        ('screen', 0), ('primary_screen', 'no'), ('name', 5), ('note', ['x']),
    ])
    def test_each_invalid_field_drops_the_card_and_keeps_the_rest(self, field, value):
        assert _kept('cards', card(**{field: value}), card(key=KEY_2)) == [card(key=KEY_2)]

    @pytest.mark.parametrize('missing', ['key', 'state'])
    def test_required_fields(self, missing):
        entry = card()
        del entry[missing]
        assert _kept('cards', entry) == []

    def test_absent_optional_fields_get_defaults(self):
        assert _kept('cards', {'key': KEY, 'state': 'lost'}) == [{
            'key': KEY, 'name': '', 'vendor_id': None, 'device_id': None, 'ticked': False,
            'state': 'lost', 'blender': False, 'backend': None, 'note': '',
            'screen': None, 'primary_screen': None,
        }]

    @pytest.mark.parametrize('state', ['ok', 'lost', 'changed', 'blender_lost', 'held', 'unticked', 'unknown'])
    def test_every_state_is_accepted(self, state):
        assert _kept('cards', card(state=state))[0]['state'] == state

    def test_lost_card_keeps_its_saved_record(self):
        lost = card(state='lost', blender=False, backend=None, screen=None)
        assert _kept('cards', lost) == [lost]

    @pytest.mark.parametrize('key', ['apple-gpu', 'pci:00000001:ff:1f.7'])
    def test_other_key_forms(self, key):
        assert _kept('cards', card(key=key))[0]['key'] == key

    @pytest.mark.parametrize('value', [0, 65535, None])
    def test_pci_id_bounds(self, value):
        assert _kept('cards', card(vendor_id=value, device_id=value))[0]['vendor_id'] == value

    def test_name_and_note_are_capped_and_sanitised(self):
        entry = _kept('cards', card(name='é' + 'n' * 200, note='`' + 'm' * 300))[0]
        assert entry['name'] == '?' + 'n' * 127
        assert entry['note'] == '?' + 'm' * 199

    def test_duplicate_key_keeps_the_first(self):
        assert _kept('cards', card(), card(name='second')) == [card()]

    def test_at_most_sixteen(self):
        keys = [f'pci:0000:{bus:02x}:00.0' for bus in range(20)]
        assert [c['key'] for c in _kept('cards', *(card(key=k) for k in keys))] == keys[:16]

    def test_not_a_dict_is_dropped(self):
        assert _kept('cards', 'card', None, card()) == [card()]


class TestAlertsNoticesWarnings:

    @pytest.mark.parametrize('field, value', [
        ('id', 'A' * 32), ('id', 'a' * 31), ('id', 'g' * 32), ('id', None),
        ('kind', 'card_new'), ('key', 'gpu0'), ('name', 1), ('detail', None),
    ])
    def test_each_invalid_alert_field_drops_the_alert(self, field, value):
        assert _kept('alerts', alert(**{field: value}), alert(id=ALERT_ID_2)) == [alert(id=ALERT_ID_2)]

    @pytest.mark.parametrize('kind', ['card_lost', 'card_changed', 'card_lost_blender', 'adapter_mismatch'])
    def test_every_alert_kind(self, kind):
        assert _kept('alerts', alert(kind=kind))[0]['kind'] == kind

    def test_null_or_absent_raised_at_keeps_the_alert(self):
        """An alert from a damaged worker store stays visible and dismissable."""
        absent = alert()
        del absent['raised_at']
        assert _kept('alerts', alert(raised_at=None)) == [alert(raised_at=None)]
        assert _kept('alerts', absent) == [alert(raised_at=None)]

    @pytest.mark.parametrize('raised_at', [
        '2026-11-19T10:00:00', 'yesterday', 1763546400, ['x'],
        '2026-11-19T10:00:00+00:00' + ' ' * 40,
    ])
    def test_invalid_raised_at_is_stored_null_and_keeps_the_alert(self, raised_at):
        """A hand-edited worker store: the card stays held there, so the admin must be able to dismiss it."""
        assert _kept('alerts', alert(raised_at=raised_at)) == [alert(raised_at=None)]

    def test_raised_at_is_normalised(self):
        assert _kept('alerts', alert(raised_at='2026-11-19T10:00:00Z'))[0]['raised_at'] == '2026-11-19T10:00:00+00:00'

    def test_duplicate_alert_id_keeps_the_first(self):
        assert _kept('alerts', alert(), alert(kind='card_changed')) == [alert()]

    def test_detail_is_capped(self):
        assert _kept('alerts', alert(detail='d' * 250))[0]['detail'] == 'd' * 200

    @pytest.mark.parametrize('field, value', [('kind', 'card_lost'), ('key', 'x'), ('name', None)])
    def test_invalid_notice_is_dropped(self, field, value):
        assert _kept('notices', notice(**{field: value}), notice(kind='card_gone')) == [notice(kind='card_gone')]

    def test_duplicate_notice_keeps_the_first(self):
        assert _kept('notices', notice(), notice(name='again')) == [notice()]

    @pytest.mark.parametrize('field, value', [
        ('code', 'Engine'), ('code', 'x' * 33), ('code', ''), ('code', 'engine-not-tied'),
        ('key', 'gpu'), ('text', None),
    ])
    def test_invalid_warning_is_dropped(self, field, value):
        assert _kept('warnings', warning(**{field: value}), warning(code='other')) == [warning(code='other')]

    def test_warning_without_key_and_long_text(self):
        kept = _kept('warnings', warning(code='engine_not_tied', key=None, text='<' + 't' * 250))
        assert kept == [{'code': 'engine_not_tied', 'key': None, 'text': '?' + 't' * 199}]

    def test_at_most_sixteen_alerts_and_warnings(self):
        alerts = [alert(id=f'{n:032x}') for n in range(18)]
        assert len(_kept('alerts', *alerts)) == 16
        assert len(_kept('warnings', *(warning() for _ in range(18)))) == 16


class TestApplyAndHelpers:

    @pytest.fixture(autouse=True)
    def _fresh_warned(self, monkeypatch):
        monkeypatch.setattr(heartbeat_cards, '_warned_workers', set())

    def _worker(self, pk=1):
        return SimpleNamespace(pk=pk, hostname='w', card_status={'schema': 1, 'cards': []})

    def test_unusable_report_warns_once_per_worker_until_a_usable_one(self, mocker):
        warning_log = mocker.patch.object(heartbeat_cards.logger, 'warning')
        debug_log = mocker.patch.object(heartbeat_cards.logger, 'debug')
        worker, other = self._worker(), self._worker(pk=2)
        for _ in range(3):
            apply_card_report(worker, {'cards': 'bad'})
        apply_card_report(other, {'cards': 'bad'})
        assert warning_log.call_count == 2
        assert debug_log.call_count == 2
        apply_card_report(worker, {'cards': report()})
        apply_card_report(worker, {'cards': 'bad'})
        assert warning_log.call_count == 3

    def test_dropped_entry_log_is_lazy_and_capped(self, mocker):
        debug_log = mocker.patch.object(heartbeat_cards.logger, 'debug')
        _kept('cards', card(key='x' * 500))
        fmt, label, item = debug_log.call_args.args
        assert '%.200r' in fmt and label == 'cards' and item['key'] == 'x' * 500
        assert len(fmt % (label, item)) < 260

    def test_applies_a_valid_report(self):
        worker = self._worker()
        assert apply_card_report(worker, {'cards': report()}) == ['card_status']
        assert worker.card_status == stored()

    @pytest.mark.parametrize('data', [{}, {'cards': 'nope'}, {'cards': report(schema=2)}])
    def test_absent_or_unusable_report_leaves_the_field(self, data):
        worker = self._worker()
        assert apply_card_report(worker, data) == []
        assert worker.card_status == {'schema': 1, 'cards': []}

    @pytest.mark.parametrize('value, expected', [
        (KEY, True), ('apple-gpu', True), ('Apple-GPU', False), ('pci:0000:1:00.0', False), (None, False),
    ])
    def test_is_card_key(self, value, expected):
        assert is_card_key(value) is expected

    @pytest.mark.parametrize('value, expected', [('0' * 32, True), ('0' * 33, False), (32, False)])
    def test_is_alert_id(self, value, expected):
        assert is_alert_id(value) is expected
