# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Plumbing for the card change browser tests (worker card selection chunk 12;
FR-CF-5 to FR-CF-7).

The manager and Chromium come from the chunk 6 harness
(``_manager_worker_list_browser``). The fake worker is the chunk 11
round-trip harness (``_card_choice_trip_harness``): a real ``config.json``,
the worker's real report, ``system_monitor.send_heartbeat`` and
``cards.remote.handle_response``. Only its heartbeat transport is replaced
here: ``HttpWorkerClient`` posts each heartbeat over HTTP with the worker
token to the same Waitress listener the page talks to, so the page, the
manager and the worker meet in one database through real requests.

``choice_trip`` is the body of each module's ``trip`` fixture (``yield
from``), as ``trip_worker`` is in chunk 11.

Module name starts with an underscore so pytest does not collect it.
"""

import json
import re
import urllib.error
import urllib.request
from types import SimpleNamespace

from django.contrib.auth.models import User
from playwright.sync_api import expect

from tests.integration.manager._card_choice_trip_harness import HOST, trip_worker

from . import _manager_worker_list_browser as lb

# worker-card-choice-form.component.ts, worker-card-choice-status.component.ts, worker-card-choice.util.ts
STALE_TEXT = "The worker's choice changed since you opened this form."
WITHDRAWN_TEXT = 'Withdrawn. If the worker had already received it, its answer will still appear here.'
PENDING_TEXT = 'Change sent less than a minute ago, waiting for the worker.'
READ_ONLY_ENV = 'The card choice on this worker is set by environment variables.'
READ_ONLY_NOT_ALLOWED = "This worker does not allow changes from the manager. Turn it on in the worker's own settings."
# Angular's 'medium' date in en-US, e.g. "Nov 19, 2026, 4:20:48 PM" (the space before PM may be U+202F).
TIME = r'\w{3} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2}\s[AP]M'
SEND_WAIT_MS = 3000


class HttpWorkerClient:
    """The ``client.post`` the trip harness calls, as a real HTTP request with the worker's token."""

    def __init__(self, base_url, token):
        self._base_url = base_url
        self._token = token

    def post(self, path, data, format='json'):
        assert format == 'json'
        request = urllib.request.Request(
            f'{self._base_url}{path}', data=json.dumps(data).encode('utf-8'), method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': f'Token {self._token}'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, content = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, content = error.code, error.read()
        return SimpleNamespace(status_code=status, content=content, data=content)


def choice_trip(monkeypatch, tmp_path, base_url):
    """Generator for a module's ``trip`` fixture: chunk 11's ``Trip``, heartbeats over HTTP to ``base_url``."""
    def make_worker_client(hostname):
        fake = lb.FakeWorker(base_url, hostname)
        return fake.row, HttpWorkerClient(base_url, fake.token), fake.token

    yield from trip_worker(monkeypatch, tmp_path, make_worker_client)


def admin_pk():
    return User.objects.get(username=lb.ADMIN_NAME).pk


def is_list_get(request):
    return request.method == 'GET' and request.url.endswith(lb.HEARTBEAT)


def record_list_gets(page):
    """The page's list polls from now on (a list that grows while the page runs)."""
    gets = []
    page.on('request', lambda request: is_list_get(request) and gets.append(request))
    return gets


def open_panel(page, base_url, hostname=HOST):
    """Log in, open the list and expand ``hostname`` while a poll lands, so the next poll is ~10 s away."""
    lb.open_worker_list(page, base_url)
    lb.wait_for_list_poll(page, lambda: lb.expand(page, hostname))
    return lb.panel(page, hostname)


def open_form(page, hostname=HOST):
    """Click Change in the worker's panel; the form."""
    panel = lb.panel(page, hostname)
    panel.locator('button.change').click()
    form = panel.locator('section.choice-form')
    form.wait_for(state='visible')
    return form


def ticks(form):
    return form.locator('mat-checkbox.card-tick')


def tick_box(form, index):
    """The native checkbox of the ``index``-th card of the form."""
    return ticks(form).nth(index).locator('input[type=checkbox]')


def expect_ticked(form, *states):
    """Each card's checkbox of the form is ticked as ``states`` says, in order."""
    expect(ticks(form)).to_have_count(len(states))
    for index, state in enumerate(states):
        expect(tick_box(form, index)).to_be_checked(checked=state)


def send(page, form, worker_pk):
    """Click Send; ``(posted body, 202 answer)``."""
    def is_post(response):
        return response.request.method == 'POST' and response.url.endswith(f'/{worker_pk}/card-choice/')

    with page.expect_response(is_post, timeout=SEND_WAIT_MS) as posted:
        form.locator('button.send').click()
    assert posted.value.status == 202, posted.value.text()
    return posted.value.request.post_data_json, posted.value.json()


def withdraw(page, panel, worker_pk):
    """Click Withdraw; the DELETE must answer 204."""
    def is_delete(response):
        return response.request.method == 'DELETE' and response.url.endswith(f'/{worker_pk}/card-choice/')

    with page.expect_response(is_delete, timeout=SEND_WAIT_MS) as deleted:
        panel.locator('button.withdraw').click()
    assert deleted.value.status == 204


def expect_sent_at_once(page, panel, hostname=HOST):
    """The pending line, Withdraw and the chip, shown from the 202 body; the form is closed."""
    expect(panel.locator('.pending')).to_have_text(PENDING_TEXT, timeout=SEND_WAIT_MS)
    expect(panel.locator('button.withdraw')).to_be_enabled()
    expect(lb.worker_row(page, hostname).locator('.change-pending-chip')).to_have_text('Change pending')
    expect(panel.locator('section.choice-form')).to_have_count(0)


def expect_nothing_pending(page, panel, hostname=HOST, timeout=SEND_WAIT_MS):
    expect(lb.worker_row(page, hostname).locator('.change-pending-chip')).to_have_count(0, timeout=timeout)
    expect(panel.locator('.pending')).to_have_count(0)
    expect(panel.locator('button.withdraw')).to_have_count(0)


def outcome_text(result, reason=None):
    """The outcome line as a full-match pattern: "Applied at <time>" or "Refused at <time>: <reason>"."""
    tail = '' if reason is None else ': ' + re.escape(reason)
    return re.compile(rf'^{result} at {TIME}{tail}$')


def requested_text(requested_by):
    return re.compile(rf'^Requested by {re.escape(requested_by)} at {TIME}\.$')
