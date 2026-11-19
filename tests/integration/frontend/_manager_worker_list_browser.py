# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Plumbing for the manager worker list browser tests (card selection chunk 6).

The real manager WSGI app (``django.core.handlers.wsgi.WSGIHandler``: the
full middleware stack, WhiteNoise serving the built Angular app, the SPA
catch-all, the real DRF views) is served by an in-process Waitress listener
on a loopback port, as ``test_waitress_body_limits`` starts one. Chromium
logs in through the real login page with a real staff user.

Each Waitress thread opens its own Django connection, so the test database
is moved to a WAL file first (``sqlite_file_db.file_backed_sqlite``): the
in-memory shared-cache test database would answer concurrent requests with
SQLITE_LOCKED (issue #272).

A fake worker is a real ``Worker`` row with a real DRF token that posts
heartbeats over HTTP to the same listener, exactly as the worker agent does.

The built Angular app must be present (``manager/frontend/dist``): the tests
skip without it, with the reason saying so.

Module name starts with an underscore so pytest does not collect it.
"""

import contextlib
import json
import threading
import urllib.request
from pathlib import Path

import pytest
import waitress
from django.contrib.auth.models import User
from django.core.handlers.wsgi import WSGIHandler
from playwright.sync_api import sync_playwright
from rest_framework.authtoken.models import Token
from waitress import wasyncore

from workers.models import Worker

from tests.integration.manager.sqlite_file_db import file_backed_sqlite

DIST_INDEX = (Path(__file__).resolve().parents[3] / 'manager' / 'frontend' / 'dist' / 'browser' / 'browser'
              / 'index.html')
needs_built_frontend = pytest.mark.skipif(
    not DIST_INDEX.is_file(),
    reason='the built Angular app is missing (run "npm ci && npm run build" in manager/frontend)')

ADMIN_NAME = 'list-admin'
ADMIN_PASSWORD = 'list-admin-pw-7d1c'
HEARTBEAT = '/api/heartbeat/'
POLL_WAIT_MS = 15_000  # the production build polls the list every 10 s


class FakeWorker:
    """A worker row with a token that posts heartbeats to the served manager."""

    def __init__(self, base_url, hostname):
        user = User.objects.create_user(username=f'worker_{hostname}')
        user.set_unusable_password()
        user.save()
        self.row = Worker.objects.create(hostname=hostname, user=user, is_active=True)
        self.token = Token.objects.create(user=user).key
        self.hostname = hostname
        self._base_url = base_url

    def beat(self, cards=None, *, register=False):
        """One heartbeat (``register``: with ``os``, the registration path); returns the answer body."""
        body = {'hostname': self.hostname, 'status': 'IDLE', 'cpu_name': 'Test CPU'}
        if register:
            body.update(os='Linux', ip_address='127.0.0.1')
        if cards is not None:
            body['cards'] = cards
        request = urllib.request.Request(
            f'{self._base_url}{HEARTBEAT}', data=json.dumps(body).encode('utf-8'), method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': f'Token {self.token}'})
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200
            return json.loads(response.read())

    def store_card_status(self, card_status):
        """Write ``card_status`` to the row directly, past the heartbeat's sanitiser."""
        Worker.objects.filter(pk=self.row.pk).update(card_status=card_status)

    def stored(self):
        """``(card_status, card_alert_dismissals)`` of the row as now saved."""
        row = Worker.objects.get(pk=self.row.pk)
        return row.card_status, row.card_alert_dismissals


def _stop(server, thread):
    """Close every dispatcher from inside the loop thread, then drain."""
    server.trigger.pull_trigger(lambda: wasyncore.close_all(server._map, ignore_all=True))
    thread.join(timeout=10)
    server.task_dispatcher.shutdown()
    assert not thread.is_alive()


@contextlib.contextmanager
def served_manager(settings, tmp_path):
    """Serve the manager on a loopback port over a WAL file database; yields the base URL.

    The caller holds ``transactional_db``. The session and CSRF cookies are
    ``Secure`` in production (Caddy terminates TLS); the listener here is
    plain HTTP, so the test turns that flag off.
    """
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    with file_backed_sqlite(tmp_path):
        User.objects.create_user(username=ADMIN_NAME, password=ADMIN_PASSWORD, is_staff=True, is_superuser=True)
        server = waitress.create_server(WSGIHandler(), host='127.0.0.1', port=0, threads=8,
                                        ident='worker-list-browser')
        thread = threading.Thread(target=server.run, name='worker-list-waitress', daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.effective_port}'
        finally:
            _stop(server, thread)


@contextlib.contextmanager
def own_chromium():
    """A Playwright and headless Chromium of the caller's own, stopped on exit (issue #277).

    Playwright's sync API keeps an asyncio loop running in the main thread
    while it is started, and Django refuses ORM calls there. pytest-playwright's
    ``page`` starts it once per session and stops it only at the end, and a
    test that also uses the database is sorted with the database tests, so
    the loop would outlive it and break every later database test. A module
    of database browser tests starts its own here, from a module-scoped
    fixture, so the loop ends with the module.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


def watch_page(page):
    """Collect page errors and console errors; the list grows while the page runs."""
    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.on('console', lambda msg: msg.type == 'error' and errors.append(msg.text))
    return errors


def log_in(page, base_url):
    """Log in through the real login page as the staff user."""
    page.goto(f'{base_url}/login')
    page.locator('input[formcontrolname=username]').fill(ADMIN_NAME)
    page.locator('input[formcontrolname=password]').fill(ADMIN_PASSWORD)
    page.locator('button[type=submit]').click()
    page.wait_for_url(f'{base_url}/')


def open_worker_list(page, base_url):
    """Log in and open the worker list; waits for the table."""
    log_in(page, base_url)
    page.goto(f'{base_url}/workers')
    page.wait_for_selector('tr.worker-row')


def worker_row(page, hostname):
    return page.locator('tr.worker-row', has_text=hostname)


def panel(page, hostname):
    """The card panel in the detail row right after the worker's row."""
    return worker_row(page, hostname).locator('xpath=following-sibling::tr[1]').locator('section.cards-panel')


def expand(page, hostname):
    worker_row(page, hostname).locator('button.expand-toggle').click()
    panel(page, hostname).wait_for(state='visible')


def border_shadow(page, hostname):
    """The computed ``box-shadow`` of the row's first cell (the red border)."""
    return worker_row(page, hostname).locator('td').first.evaluate('td => getComputedStyle(td).boxShadow')


def wait_for_list_poll(page, action=None):
    """Run ``action`` (if any), then wait for the next answered ``GET /api/heartbeat/`` of the page.

    The page renders the answer right after; callers assert with Playwright's retrying ``expect``.
    """
    def is_list_poll(response):
        return response.request.method == 'GET' and response.url.endswith(HEARTBEAT)

    with page.expect_response(is_list_poll, timeout=POLL_WAIT_MS):
        if action is not None:
            action()
