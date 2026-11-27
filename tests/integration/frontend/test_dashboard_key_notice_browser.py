# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The dashboard's one-time enrollment key notice in a real browser (setup-rebuild FR-WZ-9).

Chromium drives the built Angular app served by the real manager
(``_manager_worker_list_browser``) over a WAL file database; the notice view
and the ``ManagerSettings`` row are real. These tests use the database, so
they start their own Chromium (issue #277, ``lb.own_chromium``).
"""
import pytest
from django.contrib.auth.models import User
from playwright.sync_api import expect

from workers import enrollment_key
from workers.models import ManagerSettings

from tests.integration.manager.conftest import TEST_ENROLLMENT_KEY_CANONICAL

from . import _manager_worker_list_browser as lb

pytestmark = lb.needs_built_frontend

NOTICE_PATH = '/api/auth/enrollment_key_notice/'
VIEWER = 'notice-viewer'
VIEWER_PASSWORD = 'notice-viewer-pw-3e8b'


@pytest.fixture(scope='module')
def chromium():
    """This module's own Chromium (issue #277)."""
    with lb.own_chromium() as browser:
        yield browser


@pytest.fixture
def ui_page(chromium):
    context = chromium.new_context()
    yield context.new_page()
    context.close()


@pytest.fixture
def _orm_beside_playwright(monkeypatch):
    """ORM calls in the main thread beside this module's Playwright loop, this test only."""
    monkeypatch.setenv('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')


@pytest.fixture
def manager(_orm_beside_playwright, transactional_db, settings, tmp_path):
    """The served manager, its settings row pinned to a known key and the notice not dismissed."""
    with lb.served_manager(settings, tmp_path) as base_url:
        ManagerSettings.objects.update_or_create(
            pk=1, defaults={'manager_id': '00000000-0000-0000-0000-0000000000b2',
                            'enrollment_key': TEST_ENROLLMENT_KEY_CANONICAL,
                            'enrollment_key_notice_dismissed': False})
        yield base_url


def _notice_get(response):
    return response.request.method == 'GET' and response.url.endswith(NOTICE_PATH)


def test_admin_copies_and_dismisses_the_key_notice_for_good(manager, ui_page):
    """FR-WZ-9: an admin sees the key in display form; Copy says Copied; Dismiss hides the
    notice and it stays hidden after a reload."""
    with ui_page.expect_response(_notice_get):
        lb.log_in(ui_page, manager)
    notice = ui_page.locator('app-enrollment-key-notice mat-card')
    expect(notice).to_contain_text('Worker enrollment key')
    expect(notice.locator('code.key')).to_have_text(enrollment_key.format_display(TEST_ENROLLMENT_KEY_CANONICAL))

    notice.get_by_role('button', name='Copy').click()
    expect(notice.get_by_role('button', name='Copied')).to_be_visible()
    notice.get_by_role('button', name='Dismiss').click()
    expect(notice).to_have_count(0)
    assert ManagerSettings.objects.get(pk=1).enrollment_key_notice_dismissed is True

    with ui_page.expect_response(_notice_get):
        ui_page.reload()
    expect(ui_page.get_by_role('heading', name='Dashboard')).to_be_visible()
    expect(notice).to_have_count(0)


def test_non_admin_gets_no_notice_and_no_permission_snackbar(manager, ui_page):
    """FR-WZ-9: a user who is not staff never asks for the admin-only notice (so it adds no
    403 and no "Permission denied" snackbar) and sees none.

    The dashboard's own ``/api/stats/`` is admin-only too and still shows that snackbar to
    such a user; that is not the notice's doing, so it is not checked here."""
    User.objects.create_user(username=VIEWER, password=VIEWER_PASSWORD)
    asked = []
    ui_page.on('request', lambda request: request.url.endswith(NOTICE_PATH) and asked.append(request.url))
    ui_page.goto(f'{manager}/login')
    ui_page.locator('input[formcontrolname=username]').fill(VIEWER)
    ui_page.locator('input[formcontrolname=password]').fill(VIEWER_PASSWORD)
    ui_page.locator('button[type=submit]').click()
    ui_page.wait_for_url(f'{manager}/')

    expect(ui_page.get_by_role('heading', name='Dashboard')).to_be_visible()
    expect(ui_page.locator('app-dashboard mat-spinner')).to_have_count(0)
    expect(ui_page.locator('app-enrollment-key-notice mat-card')).to_have_count(0)
    assert asked == []
