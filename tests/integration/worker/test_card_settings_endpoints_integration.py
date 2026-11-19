# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Settings page graphics card endpoints through the real WSGI app and config store (FR-CW-4, FR-CW-5, #265).

``GET /api/cards/`` and ``POST /api/control/cards`` run the real setup gate,
the Bearer password check against a real password hash, the shutdown guard,
the body cap, ``cards_view``, ``cards.choice`` and ``cards.store``. The last
class repeats the 401 and 503 answers over the real Waitress server, so the
reason phrases of #265 are checked on the wire as well.
"""

import json
import urllib.error
import urllib.request

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.web_ui import cards_view
from sethlans_worker_agent.web_ui.runtime.handlers_cards import SAVED_TEXT
from sethlans_worker_agent.web_ui.setup import gate

from . import _card_pages_harness as pages
from ._card_pages_harness import ADDRESS_A, ADDRESS_B, ADDRESS_C, KEY_A, KEY_B, KEY_C, saved_record


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    directory = pages.install(monkeypatch, tmp_path)
    pages.bind_shutdown_event()
    yield directory
    pages.unbind_shutdown_event()


@pytest.fixture
def setup_done(data_dir):
    gate.mark_setup_complete()
    return data_dir


def _save(request, headers=None):
    return pages.call(pages.CONTROL_PATH, 'POST', request, pages.bearer() if headers is None else headers)


class TestCardSettingsGetIntegration:

    def test_get_is_anonymous_after_setup(self, setup_done):
        pages.seed(setup_done, {'method': 'gpu', 'cards': [saved_record(KEY_C)], 'known': [KEY_C], 'revision': 3})
        status, answer = pages.call('/api/cards/')
        assert status == '200 OK'
        assert (answer['method'], answer['ticked'], answer['revision']) == ('gpu', [KEY_C], 3)
        assert [card['address'] for card in answer['cards']] == [ADDRESS_A, ADDRESS_B, ADDRESS_C]
        assert [card['ticked'] for card in answer['cards']] == [False, False, True]

    def test_get_during_setup_gets_the_setup_gate_answer(self, data_dir):
        status, answer = pages.call('/api/cards/')
        assert (status, answer) == ('503 Service Unavailable', {'detail': 'Setup not complete.'})

    def test_get_writes_nothing(self, setup_done):
        for _ in range(3):
            assert pages.call('/api/cards/')[0] == '200 OK'
        assert pages.config_json(setup_done) is None


class TestCardSettingsPostIntegration:

    def test_post_without_the_password_is_401(self, setup_done):
        status, answer = _save(pages.body(), headers={})
        assert (status, answer) == ('401 Unauthorized', {'error': 'Unauthorized'})
        assert pages.config_json(setup_done) is None

    def test_post_with_a_wrong_password_is_401(self, setup_done):
        assert _save(pages.body(), headers=pages.bearer('not-the-password'))[0] == '401 Unauthorized'
        assert pages.config_json(setup_done) is None

    def test_post_with_the_password_saves(self, setup_done):
        status, answer = _save(pages.body())
        assert status == '200 OK'
        assert answer == {'status': 'ok', 'revision': 1, 'message': SAVED_TEXT}
        compute = pages.config_json(setup_done)['compute']
        assert compute['method'] == 'gpu' and compute['gpu_mode'] == 'split' and compute['revision'] == 1
        assert compute['cards'] == [saved_record(KEY_A), saved_record(KEY_C)]
        assert compute['known'] == [KEY_A, KEY_C]
        listed = pages.call('/api/cards/')[1]
        assert listed['revision'] == 1 and listed['ticked'] == [KEY_A, KEY_C]

    def test_post_keeps_the_rest_of_config_json(self, setup_done):
        (setup_done / 'config.json').write_text(json.dumps({'manager': {'url': 'https://manager.example:8080'}}),
                                                encoding='utf-8')
        assert _save(pages.body())[0] == '200 OK'
        saved = pages.config_json(setup_done)
        assert saved['manager'] == {'url': 'https://manager.example:8080'} and saved['compute']['revision'] == 1

    def test_env_override_is_409(self, setup_done, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_METHOD, 'gpu')
        status, answer = _save(pages.body())
        assert (status, answer['error']) == ('409 Conflict', cards_view.ENV_OVERRIDE_TEXT)
        assert pages.config_json(setup_done) is None

    def test_stale_revision_is_409_and_revision_rises_once(self, setup_done):
        assert _save(pages.body())[0] == '200 OK'
        before = pages.raw_bytes(setup_done)
        status, answer = _save(pages.body(cards=[ADDRESS_B], shown=[ADDRESS_B]))  # same base revision 0
        assert (status, answer['error']) == ('409 Conflict', cards_view.STALE_TEXT)
        assert pages.raw_bytes(setup_done) == before
        assert pages.config_json(setup_done)['compute']['revision'] == 1

    def test_newly_ticking_a_card_held_by_an_alert_is_400(self, setup_done):
        pages.seed(setup_done, {'method': 'cpu', 'revision': 0, 'alerts': [pages.alert(KEY_B, 'Card B')]})
        before = pages.raw_bytes(setup_done)
        status, answer = _save(pages.body(cards=[ADDRESS_A, ADDRESS_B], shown=[ADDRESS_A, ADDRESS_B]))
        assert (status, answer) == ('400 Bad Request', {'error': 'Dismiss the alert for Card B first.'})
        assert pages.raw_bytes(setup_done) == before

    def test_keeping_a_held_card_that_was_already_ticked_saves(self, setup_done):
        pages.seed(setup_done, {'method': 'gpu', 'cards': [saved_record(KEY_B)], 'revision': 4,
                                'alerts': [pages.alert(KEY_B, 'Card B', kind='card_lost_blender')]})
        status, answer = _save(pages.body(cards=[ADDRESS_B], shown=[ADDRESS_B], revision=4))
        assert status == '200 OK' and answer['revision'] == 5
        compute = pages.config_json(setup_done)['compute']
        assert compute['cards'] == [saved_record(KEY_B)] and len(compute['alerts']) == 1  # the alert stays

    def test_post_during_shutdown_is_503(self, setup_done):
        pages.bind_shutdown_event().set()
        status, answer = _save(pages.body())
        assert (status, answer) == ('503 Service Unavailable', {'error': 'Server is shutting down'})
        assert pages.config_json(setup_done) is None

    def test_unknown_cards_path_is_404(self, setup_done):
        status, answer = pages.call('/api/control/cards/x', 'POST', pages.body(), pages.bearer())
        assert (status, answer) == ('404 Not Found', {'error': 'Not Found'})
        assert pages.config_json(setup_done) is None

    def test_body_over_the_cap_is_413(self, setup_done):
        status, answer = pages.call(pages.CONTROL_PATH, 'POST', headers=pages.bearer(), raw=b'{' + b' ' * 5000 + b'}')
        assert (status, answer) == ('413 Request Entity Too Large', {'error': 'Request body too large'})
        assert pages.config_json(setup_done) is None

    def test_invalid_body_is_400(self, setup_done):
        status, answer = _save(pages.body(gpu_mode='Combined'))
        assert (status, answer) == ('400 Bad Request', {'error': 'The GPU mode must be split or combined.'})
        assert pages.config_json(setup_done) is None

    def test_post_during_setup_gets_the_setup_gate_answer(self, data_dir):
        assert _save(pages.body())[0] == '503 Service Unavailable'
        assert pages.config_json(data_dir) is None


def _post_over_http(base_url, token=None):
    headers = {'Content-Type': 'application/json'}
    if token is not None:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(base_url + pages.CONTROL_PATH, data=json.dumps(pages.body()).encode(),
                                     headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.reason, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.reason, json.loads(exc.read().decode())


class TestCardSettingsOverWaitressIntegration:

    @pytest.fixture
    def served(self, data_dir, web_ui_setup_complete):
        return data_dir, web_ui_setup_complete

    def test_401_and_save_over_the_wire(self, served):
        directory, server = served
        assert _post_over_http(server['base_url'])[:2] == (401, 'Unauthorized')
        assert pages.config_json(directory) is None
        code, _reason, answer = _post_over_http(server['base_url'], server['token'])
        assert code == 200 and answer['revision'] == 1
        assert pages.config_json(directory)['compute']['revision'] == 1

    def test_503_during_shutdown_over_the_wire(self, served):
        directory, server = served
        pages.bind_shutdown_event().set()
        assert _post_over_http(server['base_url'], server['token'])[:2] == (503, 'Service Unavailable')
        assert pages.config_json(directory) is None
