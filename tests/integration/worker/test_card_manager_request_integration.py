# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
A graphics card change from the manager against the real store and the real worker pages (FR-CM-1..8).

The requests go through ``cards.remote.handle_response`` and the worker's WSGI app into a real
``config.json`` in a temporary data dir; only the OS probe and the Blender list are faked
(``_card_pages_harness``).

- The race of the spec's concurrency notes: a settings-page save and a manager request on the same base
  revision, on two threads. Exactly one wins; the other is refused (409, or ``stale_revision``), and
  ``revision`` rises by exactly one.
- The switch endpoint (401, 400, 409 under its variable, 200) and its effect on the next request.
- The page GET shows the last manager change with ``requested_by`` as ``user id=<pk>`` (S2).
"""

import threading
import time

import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.cards import remote, store
from sethlans_worker_agent.web_ui import cards_view

from . import _card_pages_harness as pages
from ._card_pages_harness import ADDRESS_A, ADDRESS_C, KEY_A, KEY_C, saved_record

BARRIER_TIMEOUT = 10
REQUEST_ID = '0f' * 16
SWITCH_PATH = '/api/control/cards/manager-changes'


def _request(key=KEY_C, base_revision=0, **changes):
    record = saved_record(key)
    value = {'id': REQUEST_ID, 'method': 'gpu', 'gpu_mode': 'split',
             'cards': [{'key': key, 'vendor_id': record['vendor_id'], 'device_id': record['device_id']}],
             'shown': [KEY_A, KEY_C], 'base_revision': base_revision, 'requested_by': 'user id=4',
             'requested_at': '2026-11-19T11:00:00+00:00'}
    value.update(changes)
    return value


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    directory = pages.install(monkeypatch, tmp_path)
    pages.bind_shutdown_event()
    yield directory
    pages.unbind_shutdown_event()


@pytest.fixture
def overlapping_saves(monkeypatch):
    """Both saves enter ``store.save`` together; each write pauses inside the section lock."""
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    real_save, real_set_many = store.save, config_store.set_many

    def save(*args, **kwargs):
        barrier.wait()
        return real_save(*args, **kwargs)

    def set_many(pairs, expect=None):
        time.sleep(0.1)
        return real_set_many(pairs, expect=expect)

    monkeypatch.setattr(store, 'save', save)
    monkeypatch.setattr(config_store, 'set_many', set_many)


class TestPageSaveAgainstManagerRequest:

    def test_exactly_one_wins_and_the_other_is_refused(self, data_dir, overlapping_saves):
        page_body = pages.body(cards=[ADDRESS_A], shown=[ADDRESS_A, ADDRESS_C])
        page, _none = pages.run_together([
            lambda: pages.call(pages.CONTROL_PATH, 'POST', page_body, pages.bearer()),
            lambda: remote.handle_response({'id': 1, 'card_choice_request': _request()}),
        ])
        compute = pages.config_json(data_dir)['compute']
        outcome = compute['last_request']

        assert compute['revision'] == 1
        if page[0] == '200 OK':
            assert compute['cards'] == [saved_record(KEY_A)]
            assert (outcome['code'], outcome['revision']) == ('stale_revision', 1)
        else:
            assert page == ('409 Conflict', {'error': cards_view.STALE_TEXT}), page
            assert compute['cards'] == [saved_record(KEY_C)]
            assert (outcome['code'], outcome['revision']) == ('applied', 1)
        assert outcome['id'] == REQUEST_ID


class TestManagerRequestOnTheRealStore:

    def test_applied_then_shown_on_the_page(self, data_dir):
        pages.seed(data_dir, {'revision': 0})
        remote.handle_response({'card_choice_request': _request()})
        remote.handle_response({'card_choice_request': _request()})  # a repeat delivery saves nothing

        compute = pages.config_json(data_dir)['compute']
        assert compute['revision'] == 1 and compute['cards'] == [saved_record(KEY_C)]
        shown = pages.call('/api/cards/')[1]['last_request']
        assert (shown['id'], shown['result'], shown['requested_by']) == (REQUEST_ID, 'applied', 'user id=4')

    def test_stale_base_is_refused_and_only_last_request_changes(self, data_dir):
        pages.seed(data_dir, {'revision': 3, 'method': 'cpu'})
        remote.handle_response({'card_choice_request': _request(base_revision=2)})
        compute = pages.config_json(data_dir)['compute']
        assert (compute['revision'], compute['method']) == (3, 'cpu')
        assert compute['last_request']['code'] == 'stale_revision'


class TestSwitchEndpointIntegration:

    def test_needs_the_password(self, data_dir):
        assert pages.call(SWITCH_PATH, 'POST', {'allow': False})[0] == '401 Unauthorized'
        assert pages.config_json(data_dir) is None

    def test_bad_body_is_400(self, data_dir):
        assert pages.call(SWITCH_PATH, 'POST', {'allow': 'no'}, pages.bearer())[0] == '400 Bad Request'
        assert pages.config_json(data_dir) is None

    def test_409_under_its_environment_variable(self, data_dir, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_ALLOW, 'true')
        status, answer = pages.call(SWITCH_PATH, 'POST', {'allow': False}, pages.bearer())
        assert status == '409 Conflict'
        assert answer['error'] == "This switch is set by an environment variable on this worker."
        assert pages.config_json(data_dir) is None

    def test_off_refuses_the_next_request_and_on_lets_it_apply(self, data_dir):
        pages.seed(data_dir, {'revision': 2})
        assert pages.call(SWITCH_PATH, 'POST', {'allow': False}, pages.bearer())[0] == '200 OK'
        assert pages.config_json(data_dir)['compute']['revision'] == 2  # the switch is not a choice (FR-CM-2)
        remote.handle_response({'card_choice_request': _request(base_revision=2)})
        assert pages.config_json(data_dir)['compute']['last_request']['code'] == 'not_allowed'

        assert pages.call(SWITCH_PATH, 'POST', {'allow': True}, pages.bearer())[0] == '200 OK'
        remote.handle_response({'card_choice_request': _request(base_revision=2, id='1e' * 16)})
        compute = pages.config_json(data_dir)['compute']
        assert (compute['revision'], compute['last_request']['code']) == (3, 'applied')
        assert pages.call('/api/cards/')[1]['manager_changes_allowed'] is True
