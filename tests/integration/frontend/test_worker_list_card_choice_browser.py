# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager worker list, a graphics card change sent from the page and answered by
the worker, in a real browser (worker card selection chunk 12; FR-CF-5,
FR-CF-6, FR-CM-3..13).

Chromium drives the built Angular app served by the real manager and logs in
as a staff user. The fake worker is the chunk 11 round-trip harness: its real
heartbeat, report and ``cards.remote.handle_response`` against a real
``config.json``, posting over HTTP with a worker token
(``_card_choice_browser``). Each test follows one change from the form to
the worker and back to the page: applied, refused and withdrawn.

These tests use the database, so they do not use pytest-playwright's ``page``
(issue #277); they start this module's own Chromium (``lb.own_chromium``).
"""

import re

import pytest
from playwright.sync_api import expect

from sethlans_worker_agent.cards import remote, store_requests
from tests.integration.manager._card_choice_trip_harness import BASE_REVISION, BOTH
from tests.integration.manager._card_contract_harness import KEY_OK, RTX

from . import _card_choice_browser as cb
from . import _manager_worker_list_browser as lb

pytestmark = lb.needs_built_frontend

HOST = cb.HOST
ASYNC_UNSAFE = 'DJANGO_ALLOW_ASYNC_UNSAFE'


@pytest.fixture(scope='module')
def chromium():
    """This module's own Chromium, stopped when the module ends (issue #277); see ``lb.own_chromium``."""
    with lb.own_chromium() as browser:
        yield browser


@pytest.fixture
def ui_page(chromium):
    """A fresh browser context and page per test."""
    context = chromium.new_context()
    yield context.new_page()
    context.close()


@pytest.fixture
def _orm_beside_playwright(monkeypatch):
    """Allow ORM calls in the main thread while this module's Playwright loop runs, for this test only.

    Requested before ``transactional_db``, so it is restored after the database flush.
    """
    monkeypatch.setenv(ASYNC_UNSAFE, 'true')


@pytest.fixture
def manager(_orm_beside_playwright, transactional_db, settings, tmp_path):
    with lb.served_manager(settings, tmp_path) as base_url:
        yield base_url


@pytest.fixture
def trip(manager, monkeypatch, tmp_path):
    yield from cb.choice_trip(monkeypatch, tmp_path, manager)


def _keys(compute):
    return [entry['key'] for entry in compute['cards']]


def _send_both(page, trip):
    """Open the form, tick the second card, Send; returns the stored pending request."""
    form = cb.open_form(page)
    cb.tick_box(form, 1).check()
    body, answer = cb.send(page, form, trip.worker.pk)
    assert body == trip.body(keys=BOTH, gpu_mode='split')
    pending = answer['card_choice_request']
    assert answer['replaced'] is None
    assert trip.row().card_choice_request == pending
    return pending


def test_change_is_sent_applied_and_shown(manager, trip, ui_page):
    """Form from the report -> Send (pending at once) -> worker applies -> next heartbeat -> "Applied"."""
    assert trip.register()['card_choice_request'] is None
    errors = lb.watch_page(ui_page)
    panel = cb.open_panel(ui_page, manager)
    list_gets = cb.record_list_gets(ui_page)
    form = cb.open_form(ui_page)

    expect(cb.ticks(form)).to_have_text([f'{RTX} (PCI 01:00.0)', f'{RTX} (PCI 02:00.0)'])
    cb.expect_ticked(form, True, False)
    expect(form.locator('.method-select')).to_have_value('gpu')
    expect(form.locator('mat-checkbox.combine')).to_have_count(0)  # one card ticked
    cb.tick_box(form, 1).check()
    expect(form.locator('mat-checkbox.combine')).to_be_visible()  # two cards of one family
    form.locator('mat-checkbox.combine input[type=checkbox]').check()
    body, answer = cb.send(ui_page, form, trip.worker.pk)

    assert body == trip.body(keys=BOTH, gpu_mode='combined')
    pending = answer['card_choice_request']
    assert pending['requested_by'] == f'user id={cb.admin_pk()}'
    cb.expect_sent_at_once(ui_page, panel)
    assert list_gets == []  # shown from the 202 body, not by a poll
    assert trip.row().card_choice_request == pending

    # The next heartbeat carries the request; the worker applies it.
    assert trip.beat()['card_choice_request'] == pending
    after = trip.machine.compute()
    assert (after['revision'], _keys(after), after['gpu_mode']) == (BASE_REVISION + 1, BOTH, 'combined')
    assert (after['last_request']['id'], after['last_request']['result']) == (pending['id'], 'applied')
    # The following heartbeat reports the outcome; the manager clears the request.
    assert trip.beat()['card_choice_request'] is None
    assert trip.last_report()['request_outcome']['id'] == pending['id']
    assert trip.row().card_choice_request == {}

    outcome = panel.locator('.outcome')
    expect(outcome).to_have_text(cb.outcome_text('Applied'), timeout=lb.POLL_WAIT_MS)
    expect(outcome).not_to_have_class(re.compile(r'(^|\s)refused(\s|$)'))
    expect(panel.locator('.requested')).to_have_text(cb.requested_text(pending['requested_by']))
    cb.expect_nothing_pending(ui_page, panel)
    expect(panel.locator('tr.card .card-ticked')).to_have_text(['Yes', 'Yes'])
    expect(panel.locator('button.change')).to_be_enabled()
    assert errors == []


def test_refused_change_shows_the_reason(manager, trip, ui_page):
    """The worker's switch went off before delivery -> ``not_allowed`` -> "Refused at ...: <reason>"."""
    trip.register()
    errors = lb.watch_page(ui_page)
    panel = cb.open_panel(ui_page, manager)
    pending = _send_both(ui_page, trip)
    cb.expect_sent_at_once(ui_page, panel)

    store_requests.set_allow_manager_changes(False, 'settings page')
    assert trip.beat()['card_choice_request'] == pending
    after = trip.machine.compute()
    assert (after['last_request']['id'], after['last_request']['code']) == (pending['id'], 'not_allowed')
    assert (after['revision'], _keys(after)) == (BASE_REVISION, [KEY_OK])
    assert trip.beat()['card_choice_request'] is None
    assert trip.row().card_choice_outcome['code'] == 'not_allowed'

    outcome = panel.locator('.outcome.refused')
    expect(outcome).to_have_text(cb.outcome_text('Refused', remote.NOT_ALLOWED_TEXT), timeout=lb.POLL_WAIT_MS)
    expect(panel.locator('.requested')).to_have_text(cb.requested_text(pending['requested_by']))
    cb.expect_nothing_pending(ui_page, panel)
    # The same report says the switch is off: no Change button, the sentence instead (FR-CF-7).
    expect(panel.locator('.read-only')).to_have_text(cb.READ_ONLY_NOT_ALLOWED)
    expect(panel.locator('button.change')).to_have_count(0)
    expect(panel.locator('tr.card .card-ticked')).to_have_text(['Yes', 'No'])
    assert errors == []


def test_withdrawn_change_never_reaches_the_worker(manager, trip, ui_page):
    """Withdraw -> 204 -> the withdrawn text at once; the next heartbeat carries no request."""
    trip.register()
    errors = lb.watch_page(ui_page)
    panel = cb.open_panel(ui_page, manager)
    list_gets = cb.record_list_gets(ui_page)
    _send_both(ui_page, trip)
    cb.expect_sent_at_once(ui_page, panel)

    cb.withdraw(ui_page, panel, trip.worker.pk)

    expect(panel.locator('.withdrawn')).to_have_text(cb.WITHDRAWN_TEXT, timeout=cb.SEND_WAIT_MS)
    cb.expect_nothing_pending(ui_page, panel)
    assert list_gets == []  # shown from the 204 at once, not by a poll
    assert trip.row().card_choice_request == {}
    before = trip.machine.compute()
    assert trip.beat()['card_choice_request'] is None
    after = trip.machine.compute()
    assert after == before and 'last_request' not in after
    assert (after['revision'], _keys(after)) == (BASE_REVISION, [KEY_OK])

    lb.wait_for_list_poll(ui_page)
    expect(panel.locator('.withdrawn')).to_have_text(cb.WITHDRAWN_TEXT)
    cb.expect_nothing_pending(ui_page, panel)
    expect(panel.locator('.outcome')).to_have_count(0)
    expect(panel.locator('button.change')).to_be_enabled()
    assert errors == []
