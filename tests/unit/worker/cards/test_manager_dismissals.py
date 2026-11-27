# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-CR-6 / FR-CK-7: the worker applies ``dismissed_card_alerts`` from each heartbeat answer.

- An id naming an open alert removes it through ``choice.dismiss_alerts``
  (the worker page's path): one INFO line, one write, a rebuild request.
- A repeated delivery dismisses nothing twice; unknown ids are ignored.
- A malformed field is ignored with one WARNING; a missing or null field is
  silent; a failed write is logged and changes nothing.

The saved section lives in a dict (``_card_pages.fake_saved``).
"""
import pytest

from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import choice
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError
from tests.unit.worker._card_pages import fake_saved

KEY = 'pci:0000:23:00.0'
OPEN_A, OPEN_B, UNKNOWN = 'a' * 32, 'b' * 32, 'c' * 32
CHOICE_LOG = 'sethlans_worker_agent.cards.choice'


def _alert(alert_id, kind='card_lost'):
    return {'id': alert_id, 'kind': kind, 'key': KEY, 'name': 'AMD Radeon RX 7900 XT',
            'detail': 'missing from the card list', 'raised_at': '2026-11-19T08:00:00+00:00'}


@pytest.fixture
def saved(monkeypatch):
    state, writes = fake_saved(monkeypatch, {'revision': 2, 'alerts': [_alert(OPEN_A), _alert(OPEN_B, 'card_changed')]})
    return state, writes


def _lines(caplog, level):
    return [r.getMessage() for r in caplog.records if r.name == CHOICE_LOG and r.levelname == level]


class TestApplied:

    def test_open_alert_is_removed_logged_and_rebuilt(self, saved, caplog):
        state, writes = saved
        caplog.set_level('INFO', logger=CHOICE_LOG)

        removed = choice.apply_manager_dismissals({'id': 7, 'dismissed_card_alerts': [OPEN_A]})

        assert [alert['id'] for alert in removed] == [OPEN_A]
        assert [alert['id'] for alert in state['compute']['alerts']] == [OPEN_B]
        assert len(writes) == 1
        assert _lines(caplog, 'INFO') == [f"Graphics card alert {OPEN_A} dismissed from the manager: card_lost for "
                                          f"{KEY} ('AMD Radeon RX 7900 XT')."]
        assert rebuild.pending() is True

    def test_goes_through_the_pages_dismissal_path(self, saved, mocker):
        dismiss = mocker.patch.object(choice, 'dismiss_alerts', return_value=[])
        choice.apply_manager_dismissals({'dismissed_card_alerts': [OPEN_A, UNKNOWN]})
        dismiss.assert_called_once_with([OPEN_A, UNKNOWN], 'manager')

    def test_a_repeated_delivery_is_applied_once(self, saved, caplog):
        _state, writes = saved
        caplog.set_level('INFO', logger=CHOICE_LOG)
        answer = {'dismissed_card_alerts': [OPEN_A]}

        assert len(choice.apply_manager_dismissals(answer)) == 1
        rebuild.reset_for_tests()
        assert choice.apply_manager_dismissals(answer) == []

        assert len(writes) == 1 and len(_lines(caplog, 'INFO')) == 1
        assert rebuild.pending() is False

    def test_unknown_ids_are_ignored(self, saved, caplog):
        state, writes = saved
        caplog.set_level('INFO', logger=CHOICE_LOG)

        assert choice.apply_manager_dismissals({'dismissed_card_alerts': [UNKNOWN]}) == []

        assert writes == [] and len(state['compute']['alerts']) == 2
        assert [r for r in caplog.records if r.name == CHOICE_LOG] == []
        assert rebuild.pending() is False

    def test_known_and_unknown_together_remove_only_the_known(self, saved):
        state, _writes = saved
        removed = choice.apply_manager_dismissals({'dismissed_card_alerts': [UNKNOWN, OPEN_B, OPEN_A, OPEN_A]})
        assert sorted(alert['id'] for alert in removed) == [OPEN_A, OPEN_B]
        assert state['compute']['alerts'] == []

    def test_empty_list_does_nothing(self, saved, caplog):
        _state, writes = saved
        assert choice.apply_manager_dismissals({'dismissed_card_alerts': []}) == []
        assert writes == [] and _lines(caplog, 'WARNING') == []


class TestIgnored:

    @pytest.mark.parametrize('value', [
        'a' * 32, {'id': OPEN_A}, 7, True, [OPEN_A.upper()], [OPEN_A[:31]], [OPEN_A + 'a'], [7], [None],
        [OPEN_A, ['nested']], [f'{i:032x}' for i in range(17)], [' ' + OPEN_A[1:]], [OPEN_A[:31] + 'g'],
        [OPEN_A + '\n'],
    ], ids=lambda value: type(value).__name__)
    def test_malformed_field_is_ignored_with_one_warning(self, saved, caplog, value):
        state, writes = saved

        assert choice.apply_manager_dismissals({'dismissed_card_alerts': value}) == []
        assert choice.apply_manager_dismissals({'dismissed_card_alerts': value}) == []

        assert writes == [] and len(state['compute']['alerts']) == 2
        assert len(_lines(caplog, 'WARNING')) == 1
        assert rebuild.pending() is False

    def test_warns_again_after_a_well_formed_field(self, saved, caplog):
        choice.apply_manager_dismissals({'dismissed_card_alerts': 'bad'})
        choice.apply_manager_dismissals({'dismissed_card_alerts': []})
        choice.apply_manager_dismissals({'dismissed_card_alerts': 'bad'})
        assert len(_lines(caplog, 'WARNING')) == 2

    @pytest.mark.parametrize('answer', [{}, {'dismissed_card_alerts': None}, None, [], 'answer'])
    def test_missing_field_or_answer_is_silent(self, saved, caplog, answer):
        _state, writes = saved
        assert choice.apply_manager_dismissals(answer) == []
        assert writes == [] and [r for r in caplog.records if r.name == CHOICE_LOG] == []

    def test_failed_write_is_logged_and_changes_nothing(self, saved, monkeypatch, caplog):
        state, _writes = saved

        def locked(pairs, expect=None):
            raise ConfigLockTimeoutError('busy')
        monkeypatch.setattr('sethlans_worker_agent.config_store.set_many', locked)

        assert choice.apply_manager_dismissals({'dismissed_card_alerts': [OPEN_A]}) == []

        assert len(state['compute']['alerts']) == 2
        assert len(_lines(caplog, 'WARNING')) == 1
        assert rebuild.pending() is False
