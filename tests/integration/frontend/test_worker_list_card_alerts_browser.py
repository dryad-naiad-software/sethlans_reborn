# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager worker list, card alerts and Dismiss, in a real browser (card selection chunk 6;
FR-CF-2 to FR-CF-4, AC-16 to AC-18, R1).

Chromium drives the built Angular app served by the real manager
(``_manager_worker_list_browser``) and logs in as a staff user. Fake workers
post heartbeats over HTTP with a worker token; the ``cards`` report is built
by the worker's real ``cards.payload`` from a real ``config.json``
(``_card_contract_harness``, faked probes only), except where a literal
report is the point (no alerts, hostile texts). The Dismiss round trip ends
in the worker's real ``choice.apply_manager_dismissals``.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from sethlans_worker_agent.cards import choice
from tests.integration.manager._card_contract_harness import (
    ALERT_IDS, KEY_CHANGED, KEY_HELD, RX, alert, machine_compute, worker_machine,
)
from tests.unit.manager import card_report_helpers as literal

from . import _manager_worker_list_browser as lb

pytestmark = lb.needs_built_frontend

HOST = 'alert-host'
LOST_ID = ALERT_IDS['card_lost']
ONE_ALERT = [alert('card_lost', KEY_HELD, RX)]
TWO_ALERTS = ONE_ALERT + [alert('card_changed', KEY_CHANGED, 'AMD Radeon RX 7800 XT', f'was AMD Radeon RX 7800 XT, now {RX}')]
RED = 'rgb(198, 40, 40)'  # #c62828
ALERT_ROW = re.compile(r'(^|\s)card-alert-row(\s|$)')
NOT_REPORTED = 'This worker has not reported its graphics cards yet.'
# worker-cards.util.ts STATE_LABELS
STATE_LABELS = {'ok': 'OK', 'unticked': 'Not ticked', 'lost': 'Missing', 'changed': 'A different card is in this slot',
                'held': 'Held until its alert is dismissed'}
XSS_NAME = '"><img src=x onerror=window.__xss=1>'
XSS_NOTE = 'javascript:alert(1)'
ASYNC_UNSAFE = 'DJANGO_ALLOW_ASYNC_UNSAFE'


@pytest.fixture(scope='session', autouse=True)
def _orm_beside_playwright():
    """Let the test thread use the ORM while Playwright runs.

    Playwright's sync API keeps an asyncio loop in the main thread, and
    Django refuses ORM calls from a thread with a running loop
    (``SynchronousOnlyOperation``), including the session's test database
    set-up and tear-down. This is the switch pytest-playwright documents
    for Django. It is session-scoped because the database tear-down runs
    at the end of the session, and is in force only from this module on.
    """
    previous = os.environ.get(ASYNC_UNSAFE)
    os.environ[ASYNC_UNSAFE] = 'true'
    yield
    if previous is None:
        os.environ.pop(ASYNC_UNSAFE, None)
    else:
        os.environ[ASYNC_UNSAFE] = previous


@pytest.fixture
def machine(monkeypatch, tmp_path):
    yield from worker_machine(monkeypatch, tmp_path)


@pytest.fixture
def manager(transactional_db, settings, tmp_path):
    with lb.served_manager(settings, tmp_path) as base_url:
        yield base_url


def _reporting(machine, base_url, alerts=ONE_ALERT):
    """A fake worker registered with the real report of the harness machine; ``(worker, report)``."""
    machine.install()
    machine.seed(machine_compute(alerts=list(alerts)))
    worker = lb.FakeWorker(base_url, HOST)
    report = machine.report()
    assert worker.beat(report, register=True)['dismissed_card_alerts'] == []
    return worker, report


def _address(key):
    return 'PCI ' + key[len('pci:0000:'):]


def test_alert_row_has_red_border_and_chip_others_have_neither(page, manager, machine):
    """AC-18 / FR-CF-2."""
    _reporting(machine, manager)
    lb.FakeWorker(manager, 'quiet-host').beat(literal.report(alerts=[]), register=True)
    lb.FakeWorker(manager, 'silent-host').beat(register=True)
    errors = lb.watch_page(page)
    lb.open_worker_list(page, manager)

    row = lb.worker_row(page, HOST)
    expect(row).to_have_class(ALERT_ROW)
    expect(row.locator('.card-alert-chip')).to_have_text('Card alert (1)')
    assert RED in lb.border_shadow(page, HOST)
    for host in ('quiet-host', 'silent-host'):
        other = lb.worker_row(page, host)
        expect(other).not_to_have_class(ALERT_ROW)
        expect(other.locator('.card-alert-chip')).to_have_count(0)
        assert lb.border_shadow(page, host) == 'none'
    expect(page.locator('.card-alert-chip')).to_have_count(1)
    assert errors == []


def test_expanded_panel_lists_cards_states_warning_and_alert(page, manager, machine):
    """FR-CF-3 / FR-CF-4: every card with its state label, the warning, the notices, the alert with its detail."""
    _worker, report = _reporting(machine, manager)
    errors = lb.watch_page(page)
    lb.open_worker_list(page, manager)
    lb.expand(page, HOST)
    panel = lb.panel(page, HOST)

    rows = panel.locator('tr.card')
    expect(rows).to_have_count(len(report['cards']))
    for index, card in enumerate(report['cards']):
        row = rows.nth(index)
        expect(row.locator('.card-name')).to_have_text(card['name'])
        expect(row.locator('.card-address')).to_have_text(_address(card['key']))
        expect(row.locator('.card-ticked')).to_have_text('Yes' if card['ticked'] else 'No')
        expect(row.locator('.card-state')).to_have_text(STATE_LABELS[card['state']])
        expect(row.locator('.card-note')).to_have_text(card['note'])
    expect(panel.locator('.warning > span')).to_have_text([w['text'] for w in report['warnings']])
    expect(panel.locator('.notice')).to_have_text(['New card found: Matrox G200eW3',
                                                   'Card no longer present: pci:0000:c1:00.0'])
    item = panel.locator('li.alert')
    expect(item).to_have_count(1)
    expect(item.locator('.alert-kind')).to_have_text('Card missing')
    expect(item.locator('.alert-card')).to_have_text(RX)
    expect(item.locator('.alert-detail')).to_have_text('(missing from the card list)')
    expect(item.locator('.alert-time')).to_have_text(re.compile(r'^\s*since \w{3} \d{1,2}, 2026, '))
    expect(item.locator('button.dismiss')).to_be_enabled()
    assert errors == []


def test_dismiss_reaches_the_worker_and_the_pruned_alert_leaves_the_page(page, manager, machine):
    """AC-16, AC-17 and FR-CF-4 end to end: page -> manager -> worker -> manager -> page."""
    worker, _report = _reporting(machine, manager)
    errors = lb.watch_page(page)
    lb.open_worker_list(page, manager)
    lb.wait_for_list_poll(page, lambda: lb.expand(page, HOST))  # the next poll is now ~10 s away
    item = lb.panel(page, HOST).locator('li.alert')
    list_gets = []
    page.on('request', lambda r: r.method == 'GET' and r.url.endswith(lb.HEARTBEAT) and list_gets.append(r))

    with page.expect_response(lambda r: r.url.endswith(f'/{worker.row.pk}/dismiss-card-alert/')) as posted:
        item.locator('button.dismiss').click()
    assert posted.value.status == 200
    assert posted.value.request.post_data_json == {'alert_id': LOST_ID}
    expect(item.locator('.dismissal-sent')).to_have_text('Dismissal sent', timeout=3000)
    assert list_gets == []  # shown from the 200 body at once, not by a poll
    expect(item.locator('button.dismiss')).to_have_count(0)
    expect(lb.worker_row(page, HOST).locator('.card-alert-chip')).to_have_text('Card alert (1)')
    assert worker.stored()[1] == [LOST_ID]

    # The worker still reports the alert: the answer carries the dismissal.
    answer = worker.beat(machine.report())
    assert answer['dismissed_card_alerts'] == [LOST_ID]
    assert [entry['id'] for entry in choice.apply_manager_dismissals(answer)] == [LOST_ID]
    pruned = machine.report()
    assert pruned['alerts'] == []
    answer = worker.beat(pruned)
    assert answer['dismissed_card_alerts'] == [] and answer['card_alert_dismissals'] == []
    assert worker.stored()[1] == []

    row = lb.worker_row(page, HOST)
    expect(row.locator('.card-alert-chip')).to_have_count(0, timeout=lb.POLL_WAIT_MS)
    expect(row).not_to_have_class(ALERT_ROW)
    assert lb.border_shadow(page, HOST) == 'none'
    panel = lb.panel(page, HOST)
    expect(panel.locator('li.alert')).to_have_count(0)
    expect(panel.locator('.dismissal-sent')).to_have_count(0)
    held = panel.locator('tr.card').filter(has_text=_address(KEY_HELD))
    expect(held.locator('.card-state')).to_have_text('OK')  # O8: back in use once dismissed
    assert errors == []


def test_expansion_and_row_survive_a_list_poll(page, manager, machine):
    """R1 / FR-CF-2: a poll with new objects keeps the open panel and the same row elements."""
    worker, _report = _reporting(machine, manager)
    errors = lb.watch_page(page)
    lb.open_worker_list(page, manager)
    lb.expand(page, HOST)
    panel = lb.panel(page, HOST)
    row = lb.worker_row(page, HOST)
    row.evaluate('el => { el.__mark = "row"; }')
    panel.evaluate('el => { el.__mark = "panel"; }')

    machine.seed(machine_compute(alerts=list(TWO_ALERTS)))
    worker.beat(machine.report())

    expect(row.locator('.card-alert-chip')).to_have_text('Card alert (2)', timeout=lb.POLL_WAIT_MS)
    expect(panel.locator('li.alert')).to_have_count(2)
    expect(row.locator('button.expand-toggle')).to_have_attribute('aria-expanded', 'true')
    assert row.evaluate('el => el.__mark') == 'row'
    assert panel.evaluate('el => el.__mark') == 'panel'
    assert errors == []


@pytest.mark.parametrize('raw_row', [False, True], ids=['through-heartbeat', 'raw-stored-row'])
def test_hostile_card_texts_render_as_text(page, manager, raw_row):
    """C-25(k): card texts reach the page through interpolation only.

    Through the heartbeat, the manager's sanitiser turns ``<`` and ``>`` into
    ``?`` and keeps ``" = ( ) :``; the page shows exactly what is stored. The
    raw-row case writes the hostile texts to the row unchanged, so the page
    itself must keep ``<img ...>`` as text.
    """
    hostile = dict(cards=[literal.card(name=XSS_NAME, note=XSS_NOTE)],
                   alerts=[literal.alert(name=XSS_NAME, detail=XSS_NOTE)], notices=[literal.notice(name=XSS_NAME)],
                   warnings=[literal.warning(text=XSS_NAME)], problem=XSS_NAME)
    worker = lb.FakeWorker(manager, HOST)
    worker.beat(literal.report(**hostile), register=True)
    if raw_row:
        worker.store_card_status(literal.stored(**hostile))
    stored, _dismissals = worker.stored()
    name = stored['cards'][0]['name']
    assert name == (XSS_NAME if raw_row else XSS_NAME.replace('<', '?').replace('>', '?'))
    assert stored['cards'][0]['note'] == XSS_NOTE
    errors = lb.watch_page(page)
    lb.open_worker_list(page, manager)
    lb.expand(page, HOST)
    panel = lb.panel(page, HOST)

    expect(lb.worker_row(page, HOST).locator('.gpu-text')).to_have_text(name)
    expect(panel.locator('.card-name')).to_have_text(name)
    expect(panel.locator('.card-note')).to_have_text(XSS_NOTE)
    expect(panel.locator('.alert-card')).to_have_text(name)
    expect(panel.locator('.alert-detail')).to_have_text(f'({XSS_NOTE})')
    expect(panel.locator('.warning > span')).to_have_text(name)
    expect(panel.locator('.notice')).to_have_text(f'New card found: {name}')
    expect(panel.locator('.problem')).to_have_text(name)
    assert page.locator('img').count() == 0
    assert page.locator('a[href^="javascript"]').count() == 0
    assert page.evaluate('window.__xss') is None
    assert errors == []


def test_worker_that_never_reported_cards(page, manager):
    """FR-CF-3: no report yet (``card_status`` is the model default ``{}``)."""
    worker = lb.FakeWorker(manager, 'silent-host')
    worker.beat(register=True)
    assert worker.stored() == ({}, [])
    errors = lb.watch_page(page)
    lb.open_worker_list(page, manager)
    lb.expand(page, 'silent-host')
    panel = lb.panel(page, 'silent-host')

    expect(panel.locator('.no-status')).to_have_text(NOT_REPORTED)
    expect(panel.locator('tr.card, li.alert, .warning')).to_have_count(0)
    expect(lb.worker_row(page, 'silent-host').locator('.card-alert-chip')).to_have_count(0)
    assert errors == []
