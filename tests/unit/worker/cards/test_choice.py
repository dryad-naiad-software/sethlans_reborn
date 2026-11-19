# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.choice`` (FR-CM-6, FR-CK-7): one code path that saves
through ``store.save`` (or dismisses through ``store.remove_alerts``) and asks
for new slots only after it returned and the section lock is released.
"""
import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import choice, store
from sethlans_worker_agent.cards.inventory import Inventory


@pytest.fixture
def calls(mocker):
    events = []

    def fake_save(*args, **kwargs):
        events.append(('save', args, kwargs))
        return 8

    mocker.patch.object(store, 'save', side_effect=fake_save)
    mocker.patch.object(choice, '_request_rebuild', side_effect=lambda: events.append(('rebuild',)))
    return events


class TestSaveChoice:

    def test_saves_then_requests_the_rebuild(self, calls):
        inventory = object()
        request = {'id': 'r-1'}

        revision = choice.save_choice('gpu', 'split', ['pci:0000:03:00.0'], ['pci:0000:03:00.0'], 7, inventory,
                                      last_request=request)

        assert revision == 8
        assert [event[0] for event in calls] == ['save', 'rebuild']
        _name, args, kwargs = calls[0]
        assert args == ('gpu', 'split', ['pci:0000:03:00.0'], ['pci:0000:03:00.0'], inventory, 7)
        assert kwargs == {'last_request': request}

    def test_rebuild_is_requested_after_the_real_save_released_its_lock(self, mocker):
        events = []
        mocker.patch.object(config_compute, '_read_raw', return_value={'revision': 0})
        mocker.patch.object(config_store, 'set_many',
                            side_effect=lambda pairs: events.append(('write', store._section_lock.locked())))
        mocker.patch.object(choice, '_request_rebuild',
                            side_effect=lambda: events.append(('rebuild', store._section_lock.locked())))

        assert choice.save_choice('cpu', 'split', [], [], 0, Inventory()) == 1

        assert events == [('write', True), ('rebuild', False)]

    @pytest.mark.parametrize('error', [store.StaleRevision(3, 2), store.EnvOverride('env'),
                                       store.CardHeld('pci:0000:03:00.0', 'x'), ValueError('bad')])
    def test_a_refused_save_requests_no_rebuild(self, mocker, error):
        mocker.patch.object(store, 'save', side_effect=error)
        rebuild = mocker.patch.object(choice, '_request_rebuild')

        with pytest.raises(type(error)):
            choice.save_choice('gpu', 'split', [], [], 0, object())

        rebuild.assert_not_called()

    def test_real_rebuild_request_marks_a_live_rebuild_pending(self, mocker):
        mocker.patch.object(store, 'save', return_value=1)
        before = rebuild.generation()

        choice.save_choice('cpu', 'split', [], [], 0, object())

        assert rebuild.state() == (before + 1, True)


class TestDismissAlerts:
    """FR-CK-7: remove, one INFO line per alert, then a rebuild request outside the section lock."""

    ALERT = {'id': 'a' * 32, 'kind': 'card_lost', 'key': 'pci:0000:03:00.0', 'name': 'RX 7900 XT'}

    def test_removed_alert_is_logged_and_requests_a_rebuild(self, mocker, caplog):
        remove = mocker.patch.object(store, 'remove_alerts', return_value=[dict(self.ALERT)])
        caplog.set_level('INFO', logger='sethlans_worker_agent.cards.choice')

        assert choice.dismiss_alerts(['a' * 32], 'settings page') == [self.ALERT]

        remove.assert_called_once_with(['a' * 32])
        [line] = [r.getMessage() for r in caplog.records if r.name == 'sethlans_worker_agent.cards.choice']
        assert line == ("Graphics card alert " + 'a' * 32 + " dismissed from the settings page: card_lost for "
                        "pci:0000:03:00.0 ('RX 7900 XT').")
        assert rebuild.pending() is True

    def test_unknown_id_requests_no_rebuild(self, mocker):
        mocker.patch.object(store, 'remove_alerts', return_value=[])
        assert choice.dismiss_alerts(['b' * 32], 'manager') == []
        assert rebuild.pending() is False

    def test_rebuild_is_requested_after_the_section_lock_is_released(self, mocker):
        events = []
        alert = dict(self.ALERT, detail='d', raised_at='2026-11-19T00:00:00+00:00')
        mocker.patch.object(config_compute, '_read_raw', return_value={'revision': 0, 'alerts': [alert]})
        mocker.patch.object(config_store, 'set_many',
                            side_effect=lambda pairs: events.append(('write', store._section_lock.locked())))
        mocker.patch.object(choice, '_request_rebuild',
                            side_effect=lambda: events.append(('rebuild', store._section_lock.locked())))

        assert [a['id'] for a in choice.dismiss_alerts(['a' * 32], 'settings page')] == ['a' * 32]
        assert events == [('write', True), ('rebuild', False)]
