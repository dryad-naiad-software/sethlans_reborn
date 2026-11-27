# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/control/cards/dismiss`` (FR-CW-4, FR-CW-5, FR-CK-7) and ``rebuild_waiting`` in the card GET.

The dismissal takes the same Bearer password, shutdown guard and body cap
as the save, validates ``{"alert_id": "<32 hex>"}`` exactly (400, one plain
sentence), removes the alert through ``store.remove_alerts``, logs one INFO
line and requests a rebuild. An id that names no open alert is 404.
"""
import logging

import pytest

from sethlans_worker_agent import config_store
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.web_ui.runtime import handlers_cards
from tests.unit.worker._card_choice import KEY_XT_23
from tests.unit.worker._card_pages import RUNTIME_GET, call, fake_probes, fake_saved

DISMISS = '/api/control/cards/dismiss'
ALERT_ID = 'ab' * 16
ALERT = {'id': ALERT_ID, 'kind': 'card_lost', 'key': KEY_XT_23, 'name': 'AMD Radeon RX 7900 XT',
         'detail': 'missing from the card list', 'raised_at': '2026-11-19T10:00:00+00:00'}


@pytest.fixture
def saved(monkeypatch):
    return fake_saved(monkeypatch, {'revision': 2, 'alerts': [dict(ALERT)]})


@pytest.fixture
def auth(fresh_shutdown_event, bearer_header):
    return bearer_header


class TestDismiss:

    def test_dismiss_removes_logs_and_requests_a_rebuild(self, saved, auth, caplog):
        state, writes = saved
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.cards.choice'):
            status, answer = call(DISMISS, 'POST', {'alert_id': ALERT_ID}, auth)

        assert (status, answer) == (200, {'status': 'ok', 'message': handlers_cards.DISMISSED_TEXT})
        assert state['compute']['alerts'] == [] and len(writes) == 1
        assert rebuild.pending() is True
        lines = [r.getMessage() for r in caplog.records if r.name == 'sethlans_worker_agent.cards.choice']
        assert lines == [f"Graphics card alert {ALERT_ID} dismissed from the settings page: card_lost for "
                         f"{KEY_XT_23} ('AMD Radeon RX 7900 XT')."]

    def test_unknown_id_is_404_and_changes_nothing(self, saved, auth):
        state, writes = saved
        status, answer = call(DISMISS, 'POST', {'alert_id': 'cd' * 16}, auth)

        assert (status, answer) == (404, {'error': handlers_cards.UNKNOWN_ALERT_TEXT})
        assert writes == [] and len(state['compute']['alerts']) == 1 and rebuild.pending() is False

    @pytest.mark.parametrize('value', [
        [ALERT_ID], 'not json', {}, {'alert_id': ALERT_ID, 'extra': 1}, {'alert_id': ALERT_ID.upper()},
        {'alert_id': ALERT_ID[:31]}, {'alert_id': ALERT_ID + '0'}, {'alert_id': 7}, {'alert_id': None},
        {'alert_id': ALERT_ID[:31] + '\n'}, {'id': ALERT_ID},
    ], ids=['list', 'string', 'empty', 'extra-field', 'upper-case', 'short', 'long', 'number', 'null',
            'newline', 'wrong-name'])
    def test_invalid_body_is_400_with_one_sentence(self, saved, auth, value):
        state, writes = saved
        status, answer = call(DISMISS, 'POST', value, auth)

        assert status == 400
        assert answer['error'].endswith('.') and answer['error'].count('.') == 1
        assert writes == [] and rebuild.pending() is False

    def test_body_that_is_not_json_is_400(self, saved, auth):
        status, answer = call(DISMISS, 'POST', raw=b'{"alert_id": ', headers=auth)
        assert (status, answer['error']) == (400, "The request body must be a JSON object.")

    def test_needs_the_password(self, saved, fresh_shutdown_event, no_page_password):
        """FR-WK-4: with no page password stored the dismissal is refused (403)."""
        assert call(DISMISS, 'POST', {'alert_id': ALERT_ID})[0] == 403
        assert rebuild.pending() is False

    def test_refused_during_shutdown(self, saved, auth, fresh_shutdown_event):
        fresh_shutdown_event.set()
        assert call(DISMISS, 'POST', {'alert_id': ALERT_ID}, auth)[0] == 503

    def test_failed_write_is_503(self, saved, auth, monkeypatch):
        def fail(_pairs, expect=None):
            raise OSError('disk full')
        monkeypatch.setattr(config_store, 'set_many', fail)

        status, answer = call(DISMISS, 'POST', {'alert_id': ALERT_ID}, auth)

        assert (status, answer) == (503, {'error': handlers_cards.NOT_DISMISSED_TEXT})
        assert rebuild.pending() is False

    def test_other_cards_actions_are_404(self, saved, auth):
        assert call('/api/control/cards/dismissed', 'POST', {'alert_id': ALERT_ID}, auth)[0] == 404


class TestRebuildWaitingField:

    def test_get_reports_rebuild_waiting(self, saved, monkeypatch):
        fake_probes(monkeypatch)
        assert call(RUNTIME_GET)[1]['rebuild_waiting'] is False

        monkeypatch.setattr(rebuild, 'rebuild_waiting', lambda: True)
        from sethlans_worker_agent.web_ui import cards_cache
        cards_cache.reset_for_tests()
        assert call(RUNTIME_GET)[1]['rebuild_waiting'] is True
