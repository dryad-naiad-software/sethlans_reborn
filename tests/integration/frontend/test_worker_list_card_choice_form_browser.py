# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager worker list, the card change form against a live report, in a real
browser (worker card selection chunk 12; FR-CF-5, FR-CF-7, R1, R2, C-25(k)).

Same plumbing as ``test_worker_list_card_choice_browser``: the built Angular
app served by the real manager, a staff login, and the chunk 11 fake worker
posting its real report over HTTP (``_card_choice_browser``), except where a
literal report is the point (hostile texts). Covered here: a stale revision
while the form is open, the read-only sentences, and hostile texts.

These tests use the database, so they do not use pytest-playwright's ``page``
(issue #277); they start this module's own Chromium (``lb.own_chromium``).
"""

import pytest
from playwright.sync_api import expect

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import choice, store_requests
from tests.integration.manager._card_choice_trip_harness import BASE_REVISION, BOTH, fresh_inventory
from tests.integration.manager._card_contract_harness import KEY_UNTICKED
from tests.unit.manager import card_report_helpers as literal
from workers.models import Worker

from . import _card_choice_browser as cb
from . import _manager_worker_list_browser as lb

pytestmark = lb.needs_built_frontend

HOST = cb.HOST
ASYNC_UNSAFE = 'DJANGO_ALLOW_ASYNC_UNSAFE'
XSS = '"><img src=x onerror=window.__xss=1>'
FINISHED_AT = '2026-11-19T10:05:00+00:00'


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


def test_stale_revision_disables_send_until_start_again(manager, trip, ui_page):
    """R2: a save on the worker while the form is open -> the stale text, Send disabled; Start again re-snapshots."""
    trip.register()
    errors = lb.watch_page(ui_page)
    cb.open_panel(ui_page, manager)
    form = cb.open_form(ui_page)
    send = form.locator('button.send')
    expect(send).to_be_enabled()
    expect(form.locator('.stale')).to_have_count(0)

    # The worker's own settings page saves another choice; its next heartbeat reports the new revision.
    assert choice.save_choice('gpu', 'split', [KEY_UNTICKED], BOTH, BASE_REVISION, fresh_inventory()) == BASE_REVISION + 1
    trip.beat()
    assert trip.last_report()['revision'] == BASE_REVISION + 1

    expect(form.locator('.stale')).to_have_text(cb.STALE_TEXT, timeout=lb.POLL_WAIT_MS)
    expect(send).to_be_disabled()
    expect(form.locator('button.start-again')).to_be_visible()
    cb.expect_ticked(form, True, False)  # R1: the poll did not reset the form's snapshot
    expect(lb.panel(ui_page, HOST).locator('tr.card .card-ticked')).to_have_text(['No', 'Yes'])

    form.locator('button.start-again').click()

    expect(form.locator('.stale')).to_have_count(0)
    expect(form.locator('button.start-again')).to_have_count(0)
    expect(send).to_be_enabled()
    cb.expect_ticked(form, False, True)
    body, answer = cb.send(ui_page, form, trip.worker.pk)
    assert body == trip.body(keys=[KEY_UNTICKED], gpu_mode='split')
    assert body['base_revision'] == BASE_REVISION + 1
    assert answer['card_choice_request']['base_revision'] == BASE_REVISION + 1
    assert errors == []


@pytest.mark.parametrize('cause', ['switch-off', 'env-override'])
def test_read_only_worker_has_no_change_button(manager, trip, ui_page, monkeypatch, cause):
    """FR-CF-7: ``manager_changes_allowed: false`` -> no Change button, one sentence saying why."""
    if cause == 'switch-off':
        store_requests.set_allow_manager_changes(False, 'settings page')
        sentence = cb.READ_ONLY_NOT_ALLOWED
    else:
        monkeypatch.setenv(config_compute.ENV_METHOD, 'gpu')
        sentence = cb.READ_ONLY_ENV
    trip.register()
    report = trip.last_report()
    assert report['manager_changes_allowed'] is False
    assert report['env_override'] is (cause == 'env-override')
    errors = lb.watch_page(ui_page)
    panel = cb.open_panel(ui_page, manager)

    expect(panel.locator('tr.card')).to_have_count(2)
    expect(panel.locator('.read-only')).to_have_text(sentence)
    expect(panel.locator('button.change')).to_have_count(0)
    expect(panel.locator('section.choice-form')).to_have_count(0)
    cb.expect_nothing_pending(ui_page, panel)
    assert errors == []


def _hostile_outcome(worker, report, pending, raw_row):
    """Store a refusal with a hostile reason; ``(reason, requested_by)`` as stored."""
    if raw_row:
        Worker.objects.filter(pk=worker.row.pk).update(card_choice_request={}, card_choice_outcome={
            'id': pending['id'], 'result': 'refused', 'code': 'invalid', 'reason': XSS, 'revision': 3,
            'finished_at': FINISHED_AT, 'requested_by': XSS, 'requested_at': pending['requested_at']})
        return XSS, XSS
    outcome = {'id': pending['id'], 'result': 'refused', 'code': 'invalid', 'reason': XSS, 'revision': 3,
               'finished_at': FINISHED_AT}
    answer = worker.beat(literal.report(**report, request_outcome=outcome))
    assert answer['card_choice_request'] is None
    stored = Worker.objects.get(pk=worker.row.pk).card_choice_outcome
    assert stored['reason'] == XSS.replace('<', '?').replace('>', '?')
    return stored['reason'], pending['requested_by']


@pytest.mark.parametrize('raw_row', [False, True], ids=['through-heartbeat', 'raw-stored-row'])
def test_hostile_reason_and_card_name_render_as_text(manager, ui_page, raw_row):
    """C-25(k): a card name in the form and a refusal reason reach the page through interpolation only.

    Through the heartbeat the manager turns ``<`` and ``>`` into ``?``; the
    raw-row case writes the hostile texts to the row unchanged, so the page
    itself must keep ``<img ...>`` as text.
    """
    report = dict(cards=[literal.card(name=XSS), literal.card(key=literal.KEY_2, name=XSS, ticked=False)],
                  alerts=[], notices=[], warnings=[])
    worker = lb.FakeWorker(manager, HOST)
    worker.beat(literal.report(**report), register=True)
    if raw_row:
        worker.store_card_status(literal.stored(**report))
    name = worker.stored()[0]['cards'][0]['name']
    assert name == (XSS if raw_row else XSS.replace('<', '?').replace('>', '?'))
    errors = lb.watch_page(ui_page)
    panel = cb.open_panel(ui_page, manager)
    form = cb.open_form(ui_page)

    expect(cb.ticks(form)).to_have_text([f'{name} (PCI 01:00.0)', f'{name} (PCI 79:00.0)'])
    cb.tick_box(form, 1).check()
    _body, answer = cb.send(ui_page, form, worker.row.pk)
    reason, requested_by = _hostile_outcome(worker, report, answer['card_choice_request'], raw_row)

    expect(panel.locator('.outcome.refused')).to_have_text(cb.outcome_text('Refused', reason),
                                                           timeout=lb.POLL_WAIT_MS)
    expect(panel.locator('.requested')).to_have_text(cb.requested_text(requested_by))
    expect(panel.locator('.card-name')).to_have_text([name, name])
    assert ui_page.locator('img').count() == 0
    assert ui_page.evaluate('window.__xss') is None
    assert errors == []
