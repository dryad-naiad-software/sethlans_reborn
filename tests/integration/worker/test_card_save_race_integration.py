# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Two card saves on the same base revision, on two threads (spec Concurrency notes, FR-CC-4).

Whichever save takes ``store._section_lock`` first wins; the other sees the
new revision and is refused (409 on a worker page, ``StaleRevision`` on the
direct ``choice.save_choice`` path the manager's request will use, FR-CM-6).
Neither is lost silently and ``revision`` rises by exactly one.

To make the two saves overlap, ``store.save`` waits at a two-party barrier
before it runs, so both threads enter it together; ``config_store.set_many``
pauses briefly inside the section lock, which widens the window a save
without that lock would lose in. The assertions do not depend on timing.
"""

import threading
import time

import pytest

from sethlans_worker_agent import config_store
from sethlans_worker_agent.cards import choice, store
from sethlans_worker_agent.web_ui import cards_view
from sethlans_worker_agent.web_ui.setup import gate

from . import _card_pages_harness as pages
from ._card_pages_harness import ADDRESS_A, ADDRESS_C, KEY_A, KEY_C, saved_record

BARRIER_TIMEOUT = 10
_CHOICE_A = pages.body(cards=[ADDRESS_A], shown=[ADDRESS_A, ADDRESS_C])
_CHOICE_C = pages.body(cards=[ADDRESS_C], shown=[ADDRESS_A, ADDRESS_C])
_WINNER_CARDS = {KEY_A: [saved_record(KEY_A)], KEY_C: [saved_record(KEY_C)]}


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    pages.reset_wizard_state()
    directory = pages.install(monkeypatch, tmp_path)
    pages.bind_shutdown_event()
    yield directory
    pages.unbind_shutdown_event()
    pages.reset_wizard_state()


@pytest.fixture
def overlapping_saves(monkeypatch):
    """Both saves enter ``store.save`` together; each write pauses inside the section lock."""
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    real_save, real_set_many = store.save, config_store.set_many

    def save(*args, **kwargs):
        barrier.wait()
        return real_save(*args, **kwargs)

    def set_many(pairs):
        time.sleep(0.1)
        return real_set_many(pairs)

    monkeypatch.setattr(store, 'save', save)
    monkeypatch.setattr(config_store, 'set_many', set_many)
    return barrier


def _direct_save(ticked_key):
    """The direct ``choice.save_choice`` path (the manager request's path, FR-CM-6) on base revision 0."""
    inventory = cards_view.probe_now().inventory
    return lambda: choice.save_choice('gpu', 'split', [ticked_key], [KEY_A, KEY_C], 0, inventory)


def _post(path, request, headers=None):
    return lambda: pages.call(path, 'POST', request, headers)


def _assert_one_saved(data_dir, winner_key):
    compute = pages.config_json(data_dir)['compute']
    assert compute['revision'] == 1
    assert compute['cards'] == _WINNER_CARDS[winner_key]


class TestCardSaveRaceIntegration:

    def test_two_settings_page_saves_one_wins_one_409(self, data_dir, overlapping_saves):
        gate.mark_setup_complete()
        results = pages.run_together([_post(pages.CONTROL_PATH, _CHOICE_A, pages.bearer()),
                                      _post(pages.CONTROL_PATH, _CHOICE_C, pages.bearer())])
        statuses = sorted(status for status, _answer in results)
        assert statuses == ['200 OK', '409 Conflict'], results
        loser = next(answer for status, answer in results if status == '409 Conflict')
        assert loser == {'error': cards_view.STALE_TEXT}
        winner_key = KEY_A if results[0][0] == '200 OK' else KEY_C
        _assert_one_saved(data_dir, winner_key)

    def test_settings_page_save_against_direct_save_one_wins(self, data_dir, overlapping_saves):
        gate.mark_setup_complete()
        results = pages.run_together([_post(pages.CONTROL_PATH, _CHOICE_A, pages.bearer()), _direct_save(KEY_C)])
        page, direct = results
        if page[0] == '200 OK':
            assert isinstance(direct, store.StaleRevision), direct
            assert (direct.current, direct.base) == (1, 0)
            _assert_one_saved(data_dir, KEY_A)
        else:
            assert page == ('409 Conflict', {'error': cards_view.STALE_TEXT}), page
            assert direct == 1
            _assert_one_saved(data_dir, KEY_C)

    def test_wizard_save_against_direct_save_one_wins(self, data_dir, overlapping_saves):
        results = pages.run_together([_post(pages.SETUP_PATH, _CHOICE_A), _direct_save(KEY_C)])
        wizard, direct = results
        _status, checkpoints = pages.call('/api/setup/status/')
        if wizard[0] == '200 OK':
            assert isinstance(direct, store.StaleRevision), direct
            _assert_one_saved(data_dir, KEY_A)
            assert 'cards_chosen' in checkpoints['checkpoints']
        else:
            assert wizard == ('409 Conflict', {'error': cards_view.STALE_TEXT}), wizard
            assert direct == 1
            _assert_one_saved(data_dir, KEY_C)
            assert 'cards_chosen' not in checkpoints['checkpoints']

    def test_two_wizard_saves_one_wins_one_409(self, data_dir):
        """No barrier: the setup mutation lock or the revision check refuses the second (either is a 409)."""
        results = pages.run_together([_post(pages.SETUP_PATH, _CHOICE_A), _post(pages.SETUP_PATH, _CHOICE_C)])
        assert sorted(status for status, _answer in results) == ['200 OK', '409 Conflict'], results
        loser = next(answer for status, answer in results if status == '409 Conflict')
        assert loser['error'] in (cards_view.STALE_TEXT,
                                  "Setup mutation in progress; retry after current operation completes.")
        winner_key = KEY_A if results[0][0] == '200 OK' else KEY_C
        _assert_one_saved(data_dir, winner_key)
