# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.choice.save_choice`` (FR-CM-6): one code path that saves
through ``store.save`` and asks for new slots only after the save returned and
the section lock is released.
"""
import pytest

from sethlans_worker_agent import config_compute, config_store
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

    def test_real_rebuild_request_logs_that_a_restart_applies_it(self, mocker, caplog):
        mocker.patch.object(store, 'save', return_value=1)
        caplog.set_level('INFO', logger='sethlans_worker_agent.cards.choice')

        choice.save_choice('cpu', 'split', [], [], 0, object())

        assert [r.getMessage() for r in caplog.records if r.name == 'sethlans_worker_agent.cards.choice'] == \
            ["The new graphics card choice takes effect when the worker restarts."]
