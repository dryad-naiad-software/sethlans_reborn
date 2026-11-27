# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The setup page in a browser against real setup mode (setup-rebuild chunk 5).

Setup mode (``run_manager --setup``) serves the built page and runs a fake
worker program (``_setup_page_browser``); every page is at phone width.
No database is used, so pytest-playwright's session ``browser`` is used (a
module's own Playwright cannot start while that one's loop runs).
One test per behaviour: the token step, each mode's sections, submit and
finish page, and the phone layout.
"""
import configparser
import re

import pytest
from playwright.sync_api import expect

from tests.integration.manager.setup_mode.conftest import data_root  # noqa: F401
from tests.integration.manager.setup_mode.setup_process import EXIT_SAVED

from . import _setup_page_browser as sp
from ._manager_worker_list_browser import needs_built_frontend

pytestmark = needs_built_frontend
EXIT_WAIT_S = 30
KEY_REFUSED = 'ABCD-EFGH-JKMN-PQRS'
KEY_ACCEPTED = '2345-6789-ABCD-EFGH'


@pytest.fixture(scope='module')
def reader(tmp_path_factory):
    """One setup process for the tests that never submit."""
    with sp.setup_server(tmp_path_factory.mktemp('setup-reader')) as (proc, _fake):
        yield proc


def _saved_mode(proc) -> str:
    assert proc.wait(timeout=EXIT_WAIT_S) == EXIT_SAVED, proc.output()
    parser = configparser.RawConfigParser(interpolation=None)
    parser.read(proc.ini_path, encoding='utf-8')
    return parser.get('setup', 'mode')


def test_token_step_refuses_a_wrong_token_and_loads_the_form(browser, reader):
    """FR-SEC-1, FR-WZ-10: a wrong token shows the server's error; the right one shows the
    loading state while the form is read, then the form."""
    with sp.phone_page(browser) as page:
        page.goto(sp.setup_url(reader))
        sp.enter_token(page, 'not-the-setup-token')
        expect(page.locator('app-setup-token .setup-error')).to_be_visible()
        expect(page.get_by_role('heading', name='Mode')).to_have_count(0)

        held = []
        page.route('**/api/setup/form/', lambda route: held.append(route))
        with page.expect_request(lambda request: request.url.endswith('/api/setup/form/')):
            sp.enter_token(page, reader.token)
        expect(page.get_by_text("Reading this machine's settings")).to_be_visible()
        assert held, 'the form request was not held'
        held[0].continue_()
        expect(page.get_by_role('heading', name='Mode')).to_be_visible(timeout=sp.FORM_WAIT_MS)
        expect(page.get_by_text("Reading this machine's settings")).to_have_count(0)


def test_manager_mode_sections_and_restarting_finish(browser, tmp_path, data_root):  # noqa: F811
    """FR-WZ-3, OQ-9, FR-WZ-8: Manager only shows Admin, a collapsed Advanced and Data folder
    (no Worker, no Manager); the submit writes sethlans.ini and the page shows the restart."""
    with sp.setup_server(tmp_path) as (proc, fake), sp.phone_page(browser) as page:
        sp.open_form(page, proc, 'Manager only')
        for heading in ('Admin', 'Data folder'):
            expect(page.get_by_role('heading', name=heading, exact=True)).to_be_visible()
        for heading in ('Worker', 'Manager'):
            expect(page.get_by_role('heading', name=heading, exact=True)).to_have_count(0)
        expect(page.locator('mat-expansion-panel-header')).to_have_attribute('aria-expanded', 'false')

        sp.fill_admin(page)
        sp.fill_data_folder(page, data_root)
        sp.save(page)
        expect(page.get_by_text('Sethlans is restarting')).to_be_visible(timeout=sp.SUBMIT_WAIT_MS)
        assert _saved_mode(proc) == 'manager'
        assert fake.applied() == []


def test_manager_worker_mode_cards_and_worker_payload(browser, tmp_path, data_root):  # noqa: F811
    """FR-WZ-4 (worker-card-selection O1, O10), FR-WK-2, FR-SEC-6: the fake's cards are listed
    unticked, Combine appears with two ticked, the submit sends the worker payload to
    --setup-apply and no password reaches setup mode's logs."""
    with sp.setup_server(tmp_path) as (proc, fake), sp.phone_page(browser) as page:
        sp.open_form(page, proc, 'Manager and worker')
        card_a = page.get_by_role('checkbox', name=re.compile(sp.CARD_A['name']))
        card_b = page.get_by_role('checkbox', name=re.compile(sp.CARD_B['name']))
        combine = page.get_by_role('checkbox', name=re.compile('Combine GPUs'))
        expect(card_a).not_to_be_checked()
        expect(card_b).not_to_be_checked()
        expect(combine).to_have_count(0)

        page.get_by_role('radio', name='GPU', exact=True).check()
        card_a.check()
        expect(combine).to_have_count(0)
        card_b.check()
        combine.check()
        sp.fill_admin(page)
        sp.fill_worker_password(page)
        sp.fill_data_folder(page, data_root)
        sp.save(page)
        expect(page.get_by_text('Sethlans is restarting')).to_be_visible(timeout=sp.SUBMIT_WAIT_MS)

        assert _saved_mode(proc) == 'manager_worker'
        [payload] = fake.applied()
        assert payload == {'compute': 'gpu', 'cards': [sp.CARD_A['address'], sp.CARD_B['address']],
                           'combine': True, 'unreal_enabled': False, 'ui_password': sp.WORKER_PASSWORD}
        logged = sp.logs_and_console(proc)
        assert sp.WORKER_PASSWORD not in logged
        assert sp.ADMIN_PASSWORD not in logged


def test_worker_mode_enrolls_after_a_refused_key(browser, tmp_path, data_root):  # noqa: F811
    """FR-WZ-3, FR-RS-3, FR-WZ-8, FR-WZ-2: discovered managers show name, host, id and port;
    a refused key shows under the key field and a second submit is taken; the finish page
    shows the manager's name, id and fingerprint and that the page is done."""
    answers = ((1, sp.INVALID_KEY), (0, sp.ENROLLED))
    with sp.setup_server(tmp_path, answers) as (proc, fake), sp.phone_page(browser) as page:
        sp.open_form(page, proc, 'Worker only')
        farm = page.get_by_role('radio', name=re.compile(sp.FARM['name']))
        found = page.locator('mat-radio-button', has=farm)
        for text in (sp.FARM['name'], f"{sp.FARM['host']}, port {sp.FARM['port']}", sp.FARM['manager_id']):
            expect(found).to_contain_text(text)

        farm.check()
        key = page.get_by_label('Enrollment key')
        key.fill(KEY_REFUSED)
        sp.fill_worker_password(page)
        sp.fill_data_folder(page, data_root)
        sp.save(page)
        under_key = page.locator('mat-form-field', has=key).locator(
            'xpath=following-sibling::app-setup-field-errors[1]')
        expect(under_key).to_have_text(sp.INVALID_KEY['message'], timeout=sp.SUBMIT_WAIT_MS)

        key.fill(KEY_ACCEPTED)
        sp.save(page)
        finish = page.locator('app-setup-finish')
        expect(finish).to_contain_text(f"This worker is enrolled with {sp.FARM['name']}.",
                                       timeout=sp.SUBMIT_WAIT_MS)
        for text in (sp.FARM['manager_id'], sp.FINGERPRINT, 'This page will now close'):
            expect(finish).to_contain_text(text)

        assert _saved_mode(proc) == 'worker'
        refused, accepted = fake.applied()
        assert refused['enrollment_key'] == KEY_REFUSED.replace('-', '')
        assert accepted['enrollment_key'] == KEY_ACCEPTED.replace('-', '')
        assert accepted['manager_url'] == f"https://{sp.FARM['ip']}:{sp.FARM['port']}"
        assert accepted['manager_name'] == sp.FARM['name']


def test_saved_card_not_on_this_machine_must_be_unticked(browser, tmp_path, data_root):  # noqa: F811
    """FR-WZ-4 (worker-card-selection O10; frontend review B1): a saved card this machine no
    longer has stays ticked as "not found on this machine", the submit is refused while it is
    ticked, and once it is unticked the submit goes through with the remaining card."""
    with sp.setup_server(tmp_path, ((0, sp.ENROLLED),), sp.SAVED_GONE_CARD) as (proc, fake), \
            sp.phone_page(browser) as page:
        sp.open_form(page, proc, 'Worker only')
        gone = page.get_by_role('checkbox', name=re.compile('not found on this machine'))
        expect(gone).to_be_checked()
        expect(page.locator('mat-checkbox.missing-card')).to_contain_text(sp.GONE_CARD)
        expect(page.get_by_role('checkbox', name=re.compile(sp.CARD_A['name']))).to_be_checked()

        page.get_by_role('radio', name=re.compile(sp.FARM['name'])).check()
        page.get_by_label('Enrollment key').fill(KEY_ACCEPTED)
        sp.fill_worker_password(page)
        sp.fill_data_folder(page, data_root)
        sp.save(page)
        expect(page.get_by_text(f'Not a graphics card of this machine: {sp.GONE_CARD}')).to_be_visible(
            timeout=sp.SUBMIT_WAIT_MS)
        assert fake.applied() == []

        gone.uncheck()
        expect(gone).to_have_count(0)
        sp.save(page)
        expect(page.locator('app-setup-finish')).to_contain_text('This page will now close',
                                                                 timeout=sp.SUBMIT_WAIT_MS)
        [payload] = fake.applied()
        assert payload['cards'] == [sp.CARD_A['address']]


@pytest.mark.parametrize('mode_label', ['Manager and worker', 'Worker only'])
def test_form_fits_phone_width(browser, reader, mode_label):
    """FR-WZ-10 (chunk 5, phone width): the form scrolls only vertically at 390 px, with the
    card list (manager and worker) and the discovered managers with their long ids (worker)."""
    with sp.phone_page(browser) as page:
        sp.open_form(page, reader, mode_label)
        widths = page.evaluate('[document.documentElement.scrollWidth, document.documentElement.clientWidth]')
        assert widths[0] <= widths[1], widths
