# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the reported card-change outcome (worker card selection
FR-CM-13, AC-29, AC-33): field validation, what an accepted outcome
changes on the row, and one WARNING per unknown id.
"""

from types import SimpleNamespace

import pytest

from workers.views import heartbeat_card_outcome as mod

A, B, C, X = 'a' * 32, 'b' * 32, 'c' * 32, 'f' * 32
WHEN = '2026-11-19T12:00:05+00:00'


def _outcome(**overrides):
    values = {'id': A, 'result': 'applied', 'code': 'applied', 'reason': '', 'revision': 4, 'finished_at': WHEN}
    values.update(overrides)
    return values


def _data(raw):
    return {'hostname': 'h', 'cards': {'schema': 1, 'request_outcome': raw}}


def _issued(*ids):
    return [{'id': i, 'requested_by': f'user id={n}', 'requested_at': f'2026-11-19T12:00:0{n}+00:00'}
            for n, i in enumerate(ids, start=1)]


def _row(pending=None, issued=(), stored=None):
    return {'card_choice_request': {'id': pending} if pending else {},
            'card_choice_issued': _issued(*issued), 'card_choice_outcome': stored or {}}


class TestReportedOutcome:

    def test_valid_outcome_is_kept(self):
        assert mod.reported_outcome(_data(_outcome())) == _outcome()

    def test_refused_outcome_with_reason(self):
        raw = _outcome(result='refused', code='stale_revision', reason='the choice was changed <b>')
        assert mod.reported_outcome(_data(raw))['reason'] == 'the choice was changed ?b?'

    def test_unknown_keys_are_ignored_and_not_kept(self):
        kept = mod.reported_outcome(_data({**_outcome(), 'requested_by': 'admin', 'extra': 1}))
        assert kept == _outcome()

    def test_finished_at_is_normalised(self):
        assert mod.reported_outcome(_data(_outcome(finished_at='2026-11-19T12:00:05Z')))['finished_at'] == WHEN

    @pytest.mark.parametrize('data', [
        None, [], {'cards': None}, {'cards': {'request_outcome': None}}, {'cards': {'request_outcome': 'x'}},
        {'hostname': 'h'},
    ])
    def test_absent_outcome(self, data):
        assert mod.reported_outcome(data) is None

    @pytest.mark.parametrize('overrides', [
        {'id': 'A' * 32}, {'id': 'a' * 31}, {'id': 7},
        {'result': 'done'}, {'code': 'other'}, {'code': None},
        {'result': 'applied', 'code': 'invalid'}, {'result': 'refused', 'code': 'applied'},
        {'reason': None}, {'reason': 'x' * 201},
        {'revision': True}, {'revision': -1}, {'revision': 2 ** 31}, {'revision': '4'},
        {'finished_at': '2026-11-19T12:00:05'}, {'finished_at': 'yesterday'}, {'finished_at': 5},
        {'finished_at': '2026-11-19T12:00:05.123456+00:00' + ' ' * 20},
    ])
    def test_any_field_outside_the_rules_drops_the_outcome(self, overrides):
        assert mod.reported_outcome(_data(_outcome(**overrides))) is None

    @pytest.mark.parametrize('missing', ['id', 'result', 'code', 'reason', 'revision', 'finished_at'])
    def test_missing_field_drops_the_outcome(self, missing):
        raw = _outcome()
        del raw[missing]
        assert mod.reported_outcome(_data(raw)) is None


class TestEffect:

    def test_unknown_id_has_no_effect(self):
        assert mod.outcome_effect(_row(pending=A, issued=[A]), _outcome(id=X)) is None

    def test_outcome_for_the_pending_id_clears_it_and_is_stored(self):
        clear, record = mod.outcome_effect(_row(pending=A, issued=[A]), _outcome())
        assert clear is True
        assert record == {**_outcome(), 'requested_by': 'user id=1', 'requested_at': '2026-11-19T12:00:01+00:00'}

    def test_late_outcome_for_a_replaced_id_keeps_the_new_pending(self):
        """AC-29: A was replaced by B; A's outcome is shown, B stays pending."""
        clear, record = mod.outcome_effect(_row(pending=B, issued=[A, B]), _outcome())
        assert clear is False
        assert record['id'] == A and record['requested_by'] == 'user id=1'

    def test_outcome_for_a_withdrawn_id_is_stored(self):
        clear, record = mod.outcome_effect(_row(issued=[A]), _outcome())
        assert (clear, record['id']) == (False, A)

    def test_repeat_changes_nothing(self):
        assert mod.outcome_effect(_row(issued=[A], stored=_outcome()), _outcome()) == (False, None)

    def test_older_outcome_does_not_replace_a_newer_one(self):
        assert mod.outcome_effect(_row(pending=C, issued=[A, B, C], stored=_outcome(id=B)), _outcome()) == (False, None)

    def test_newer_outcome_replaces_an_older_one(self):
        clear, record = mod.outcome_effect(_row(issued=[A, B], stored=_outcome()), _outcome(id=B))
        assert (clear, record['id']) == (False, B)

    def test_stored_outcome_no_longer_issued_is_replaced(self):
        clear, record = mod.outcome_effect(_row(issued=[B], stored=_outcome(id=A)), _outcome(id=B))
        assert record['id'] == B

    @pytest.mark.parametrize('issued', [None, {}, 'x', [None, 'a']])
    def test_damaged_issued_list_counts_as_empty(self, issued):
        row = {'card_choice_request': {}, 'card_choice_issued': issued, 'card_choice_outcome': {}}
        assert mod.outcome_effect(row, _outcome()) is None


class TestWarnings:

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        monkeypatch.setattr(mod, '_warned_unknown', {})

    def test_one_warning_per_unknown_id(self, mocker):
        warn, debug = mocker.patch.object(mod.logger, 'warning'), mocker.patch.object(mod.logger, 'debug')
        worker = SimpleNamespace(pk=1, hostname='host-1')
        for _ in range(3):
            mod.warn_unknown(worker, X)
        assert warn.call_count == 1 and debug.call_count == 2
        assert 'host-1' in warn.call_args.args[0] % warn.call_args.args[1:]
        mod.warn_unknown(worker, C)
        mod.warn_unknown(SimpleNamespace(pk=2, hostname='host-2'), X)
        assert warn.call_count == 3

    def test_alternating_unknown_ids_warn_once_each(self, mocker):
        warn = mocker.patch.object(mod.logger, 'warning')
        mocker.patch.object(mod.logger, 'debug')
        worker = SimpleNamespace(pk=1, hostname='host-1')
        for _ in range(5):
            mod.warn_unknown(worker, X)
            mod.warn_unknown(worker, C)
        assert warn.call_count == 2

    def test_history_is_bounded_per_worker(self, mocker):
        warn = mocker.patch.object(mod.logger, 'warning')
        worker = SimpleNamespace(pk=1, hostname='host-1')
        ids = [f'{n:032x}' for n in range(mod.WARNED_IDS_PER_WORKER + 1)]
        for outcome_id in ids:
            mod.warn_unknown(worker, outcome_id)
        assert len(mod._warned_unknown[1]) == mod.WARNED_IDS_PER_WORKER
        mod.warn_unknown(worker, ids[0])  # forgotten, so it warns again
        assert warn.call_count == len(ids) + 1

    def test_outcome_log_line(self, mocker):
        info = mocker.patch.object(mod.logger, 'info')
        mod.log_outcome('host-1', _outcome(result='refused', code='card_held'))
        assert info.call_args.args[0] % info.call_args.args[1:] == (
            f'Worker host-1 refused graphics card change {A} (card_held).')
