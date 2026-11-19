# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager-changes switch and the "Last change from the manager" line in a real browser (card selection D18, S2).

Chromium drives ``/`` and ``/setup`` of the real worker WSGI app (``_worker_cards_browser``):
``GET /api/cards/``, the Bearer-checked ``POST /api/control/cards/manager-changes``
and ``GET``/``POST /api/setup/worker/cards/`` are the real handlers, and a save
lands in the real ``config.json`` (``compute.allow_manager_changes``).
``SETHLANS_COMPUTE_ALLOW_MANAGER_CHANGES`` is unset unless a test sets it.
"""

import pytest

from sethlans_worker_agent import config_compute

from . import _worker_cards_browser as ui
from ._worker_cards_browser import HIP_1

SWITCH_PATH = '/api/control/cards/manager-changes'
SETUP_CARDS = '/api/setup/worker/cards/'
SAVED_TEXT = "Saved. The manager's next graphics card change follows this switch."
ENV_TEXT = "This switch is set by an environment variable on this worker."
HELP_TEXT = "Alerts can still be dismissed from the manager."
XSS_REASON = '<img src=x onerror="window.__xss=3">'
REQUESTED_AT = '2026-11-19T08:00:00+00:00'


@pytest.fixture
def settings_ui(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path, setup_complete=True, checkpoints=()) as served:
        yield served


@pytest.fixture
def wizard_ui(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path) as served:
        yield served


def _open_settings(page, base_url):
    errors = ui.watch_page(page)
    page.add_init_script(f"sessionStorage.setItem('workerUIPassword', {ui.PASSWORD!r})")
    page.goto(f'{base_url}/')
    page.wait_for_selector('#gc-mgr', state='visible')
    return errors


def _open_wizard(page, base_url):
    errors = ui.watch_page(page)
    page.goto(f'{base_url}/setup')
    page.wait_for_selector('#step-cards.active #cards-form', state='visible')
    return errors


def _saved_switch(data_dir):
    """``compute.allow_manager_changes`` in ``config.json``, or ``None`` when it was never saved."""
    compute = ui.compute_section(data_dir)
    return None if compute is None else compute.get('allow_manager_changes')


def _flip(page):
    """The input is drawn as a slider, so click the label the way a person does."""
    page.click('label[for=gc-mgr-on]')


def _record(result, *, reason='', requested_by='user id=7'):
    return {'id': 'fedcba9876543210fedcba9876543210', 'result': result,
            'code': 'applied' if result == 'applied' else 'not_allowed', 'reason': reason, 'revision': 0,
            'requested_by': requested_by, 'requested_at': REQUESTED_AT, 'finished_at': REQUESTED_AT}


def _local(page, iso):
    return page.evaluate(f'new Date({iso!r}).toLocaleString()')


def test_switch_off_and_save_writes_config_json(page, settings_ui):
    base_url, data_dir = settings_ui
    errors = _open_settings(page, base_url)
    posts = ui.record_posts(page, SWITCH_PATH)
    assert page.inner_text('#gc-mgr-help') == HELP_TEXT
    assert page.is_checked('#gc-mgr-on') and page.is_enabled('#gc-mgr-on')  # default on (D18)
    assert page.is_disabled('#gc-mgr-save')
    assert not page.locator('#gc-mgr-env').is_visible() and not page.locator('#gc-mgr-last').is_visible()
    _flip(page)
    assert page.is_enabled('#gc-mgr-save')
    page.click('#gc-mgr-save')
    page.wait_for_function("document.getElementById('gc-mgr-result').textContent.indexOf('Saved') === 0")
    assert page.inner_text('#gc-mgr-result') == SAVED_TEXT
    assert posts == [({'allow': False}, f'Bearer {ui.PASSWORD}')]
    assert _saved_switch(data_dir) is False
    assert not page.is_checked('#gc-mgr-on') and page.is_disabled('#gc-mgr-save')
    # Separate from the card choice and the Unreal switch.
    assert page.inner_text('#gc-result') == '' and page.inner_text('#gc-unreal-result') == ''
    assert ui.compute_section(data_dir).get('revision', 0) == 0
    assert errors == []


def test_switch_set_by_environment_is_read_only(page, settings_ui, monkeypatch):
    base_url, data_dir = settings_ui
    monkeypatch.setenv(config_compute.ENV_ALLOW, 'false')
    errors = _open_settings(page, base_url)
    assert not page.is_checked('#gc-mgr-on') and page.is_disabled('#gc-mgr-on')
    assert page.inner_text('#gc-mgr-env') == ENV_TEXT
    assert not page.locator('#gc-mgr-save').is_visible()
    assert _saved_switch(data_dir) is None
    assert errors == []


def test_switch_refused_when_environment_set_after_load(page, settings_ui, monkeypatch):
    base_url, data_dir = settings_ui
    errors = _open_settings(page, base_url)
    _flip(page)
    monkeypatch.setenv(config_compute.ENV_ALLOW, 'true')
    page.click('#gc-mgr-save')
    page.wait_for_function("document.getElementById('gc-mgr-result').textContent.indexOf('This switch') === 0")
    assert page.inner_text('#gc-mgr-result') == ENV_TEXT
    page.wait_for_selector('#gc-mgr-env', state='visible')
    assert page.is_disabled('#gc-mgr-on') and page.is_checked('#gc-mgr-on')
    assert _saved_switch(data_dir) is None
    assert errors == []


def test_last_change_line_applied(page, settings_ui):
    base_url, data_dir = settings_ui
    ui.pages.seed(data_dir, {'last_request': _record('applied')})
    errors = _open_settings(page, base_url)
    expected = f'Last change from the manager: applied, requested by user id=7 at {_local(page, REQUESTED_AT)}'
    assert page.inner_text('#gc-mgr-last') == expected
    assert errors == []


def test_last_change_line_refused_shows_reason_as_text(page, settings_ui):
    """The reason is put in with textContent; no requested_by means no "requested by" part."""
    base_url, data_dir = settings_ui
    ui.pages.seed(data_dir, {'last_request': _record('refused', reason=XSS_REASON, requested_by=None)})
    errors = _open_settings(page, base_url)
    expected = f'Last change from the manager: refused: {XSS_REASON} at {_local(page, REQUESTED_AT)}'
    assert page.inner_text('#gc-mgr-last') == expected
    assert page.locator('#gc-mgr img').count() == 0
    assert page.evaluate('window.__xss') is None
    assert errors == []


def test_slow_poll_refreshes_line_and_read_only_state_but_keeps_unsaved_flip(page, settings_ui, monkeypatch):
    base_url, data_dir = settings_ui
    page.clock.install()
    errors = _open_settings(page, base_url)
    _flip(page)  # off, not saved
    ui.pages.seed(data_dir, {'last_request': _record('refused', reason='The switch is off.')})
    page.clock.fast_forward(16_000)
    page.wait_for_selector('#gc-mgr-last', state='visible')
    assert page.inner_text('#gc-mgr-last').startswith('Last change from the manager: refused: The switch is off, '
                                                      'requested by user id=7 at ')
    assert not page.is_checked('#gc-mgr-on') and page.is_enabled('#gc-mgr-save')  # the flip stays
    monkeypatch.setenv(config_compute.ENV_ALLOW, 'true')
    page.clock.fast_forward(16_000)
    page.wait_for_selector('#gc-mgr-env', state='visible')
    assert page.is_checked('#gc-mgr-on') and page.is_disabled('#gc-mgr-on')  # the variable wins
    assert not page.locator('#gc-mgr-save').is_visible()
    assert errors == []


def test_wizard_sends_the_switch(page, wizard_ui):
    base_url, data_dir = wizard_ui
    errors = _open_wizard(page, base_url)
    posts = ui.record_posts(page, SETUP_CARDS)
    assert page.is_checked('#cards-allow') and page.is_enabled('#cards-allow')  # default on (D18)
    assert page.inner_text('#cards-allow-help') == HELP_TEXT
    assert not page.locator('#cards-allow-env').is_visible()
    page.uncheck('#cards-allow')
    page.click('#btn-cards-next')
    page.wait_for_selector('#step-verify.active')
    assert posts[0][0]['allow_manager_changes'] is False
    assert _saved_switch(data_dir) is False
    assert errors == []


def test_wizard_shows_the_saved_switch(page, wizard_ui):
    base_url, data_dir = wizard_ui
    ui.pages.seed(data_dir, {'allow_manager_changes': False, 'method': 'gpu', 'cards': [ui.record_of(HIP_1)],
                             'known': [ui.key_of(HIP_1)], 'revision': 1})
    errors = _open_wizard(page, base_url)
    assert not page.is_checked('#cards-allow')
    assert errors == []


def test_wizard_switch_set_by_environment_is_read_only_and_not_sent(page, wizard_ui, monkeypatch):
    base_url, data_dir = wizard_ui
    monkeypatch.setenv(config_compute.ENV_ALLOW, 'false')
    errors = _open_wizard(page, base_url)
    posts = ui.record_posts(page, SETUP_CARDS)
    assert not page.is_checked('#cards-allow') and page.is_disabled('#cards-allow')
    assert page.inner_text('#cards-allow-env') == ENV_TEXT
    page.click('#btn-cards-next')
    page.wait_for_selector('#step-verify.active')
    assert 'allow_manager_changes' not in posts[0][0]
    assert _saved_switch(data_dir) is None
    assert errors == []
