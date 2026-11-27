# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal switch in the worker settings page "Graphics Cards" section, in a real browser (card selection O14).

Chromium drives ``/`` of the real worker WSGI app after setup
(``_worker_cards_browser``): ``GET /api/cards/`` and the Bearer-checked
``POST /api/control/cards/unreal`` are the real handlers, and a save lands in
the real ``config.json`` (``unreal.enabled``, ``unreal_switch.KEY``). The
running value (``config_unreal.ENABLED``) is off unless a test says otherwise;
``SETHLANS_UNREAL_ENABLED`` is unset unless a test sets it.
"""

import pytest

from sethlans_worker_agent import config_unreal, unreal_switch

from . import _worker_cards_browser as ui
from ._worker_cards_browser import CUDA_1

UNREAL_PATH = '/api/control/cards/unreal'
SAVED_TEXT = "Saved. The Unreal switch takes effect at the next worker start."
ENV_TEXT = "This switch is set by an environment variable on this worker."
RESTART_TEXT = "Restart the worker for this change to take effect."


@pytest.fixture
def worker_ui(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path) as served:
        yield served


def _open_settings(page, base_url):
    errors = ui.watch_page(page)
    page.add_init_script(f"sessionStorage.setItem('workerUIPassword', {ui.PASSWORD!r})")
    page.goto(f'{base_url}/')
    page.wait_for_selector('#gc-unreal', state='visible')
    return errors


def _saved_switch(data_dir):
    """``unreal.enabled`` in ``config.json``, or ``None`` when it was never saved."""
    saved = ui.pages.config_json(data_dir)
    return None if saved is None else saved.get('unreal', {}).get('enabled')


def _flip(page):
    """Flip the switch the way a person does: its input is drawn as a slider, so click the label."""
    page.click('label[for=gc-unreal-on]')


def _wait_unreal_result(page, start):
    page.wait_for_function(f"document.getElementById('gc-unreal-result').textContent.indexOf({start!r}) === 0")


def test_switch_on_and_save_writes_config_json(page, worker_ui):
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url)
    posts = ui.record_posts(page, UNREAL_PATH)
    assert page.inner_text('#gc-help') == ui.SETTINGS_HELP_TEXT
    assert not page.is_checked('#gc-unreal-on') and page.is_enabled('#gc-unreal-on')
    assert page.is_disabled('#gc-unreal-save')  # nothing changed yet
    assert not page.locator('#gc-unreal-restart').is_visible() and not page.locator('#gc-unreal-env').is_visible()
    _flip(page)
    assert page.is_checked('#gc-unreal-on') and page.is_enabled('#gc-unreal-save')
    page.click('#gc-unreal-save')
    _wait_unreal_result(page, 'Saved')
    assert page.inner_text('#gc-unreal-result') == SAVED_TEXT
    assert posts == [({'enabled': True}, f'Bearer {ui.PASSWORD}')]
    assert _saved_switch(data_dir) is True
    # Saved on, still off in the running worker: the restart line shows and the save is done.
    assert page.inner_text('#gc-unreal-restart') == RESTART_TEXT
    assert page.is_checked('#gc-unreal-on') and page.is_disabled('#gc-unreal-save')
    # The card choice is separate: the switch save leaves it alone and its result line empty.
    assert 'compute' not in ui.pages.config_json(data_dir) and page.inner_text('#gc-result') == ''
    assert errors == []


def test_switch_save_does_not_send_the_card_choice(page, worker_ui):
    """The card Save and the switch Save are separate: ticking a card is not sent by the switch save."""
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url)
    card_posts = ui.record_posts(page, '/api/control/cards')
    ui.card_box(page, 'gc-items', CUDA_1).check()
    _flip(page)
    page.click('#gc-unreal-save')
    _wait_unreal_result(page, 'Saved')
    assert card_posts == [] and 'compute' not in ui.pages.config_json(data_dir)
    assert ui.card_box(page, 'gc-items', CUDA_1).is_checked()  # the unsaved tick stays
    assert errors == []


def test_switch_set_by_environment_is_read_only(page, worker_ui, monkeypatch):
    base_url, data_dir = worker_ui
    monkeypatch.setenv(unreal_switch.ENV_VAR, 'true')
    errors = _open_settings(page, base_url)
    assert page.is_checked('#gc-unreal-on') and page.is_disabled('#gc-unreal-on')
    assert page.inner_text('#gc-unreal-env') == ENV_TEXT
    assert not page.locator('#gc-unreal-save').is_visible()
    assert page.inner_text('#gc-unreal-restart') == RESTART_TEXT  # on from the variable, off while running
    assert _saved_switch(data_dir) is None
    assert errors == []


def test_switch_refused_when_environment_set_after_load(page, worker_ui, monkeypatch):
    """A 409 shows the server's sentence and the page then shows the switch read-only."""
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url)
    _flip(page)
    monkeypatch.setenv(unreal_switch.ENV_VAR, 'false')
    page.click('#gc-unreal-save')
    _wait_unreal_result(page, 'This switch')
    assert page.inner_text('#gc-unreal-result') == ENV_TEXT
    page.wait_for_selector('#gc-unreal-env', state='visible')
    assert page.is_disabled('#gc-unreal-on') and not page.is_checked('#gc-unreal-on')
    assert _saved_switch(data_dir) is None
    assert errors == []


def test_restart_line_when_saved_value_differs_from_running(page, worker_ui, monkeypatch):
    base_url, _data_dir = worker_ui
    monkeypatch.setattr(config_unreal, 'ENABLED', True)  # running on, nothing saved: the next start is off
    errors = _open_settings(page, base_url)
    assert not page.is_checked('#gc-unreal-on')
    assert page.locator('#gc-unreal-restart').is_visible()
    assert page.inner_text('#gc-unreal-restart') == RESTART_TEXT
    # The switch works from the keyboard.
    page.focus('#gc-unreal-on')
    page.keyboard.press('Space')
    assert page.is_checked('#gc-unreal-on') and page.is_enabled('#gc-unreal-save')
    assert errors == []


def test_slow_poll_refreshes_restart_line_but_keeps_unsaved_switch(page, worker_ui, monkeypatch):
    base_url, _data_dir = worker_ui
    page.clock.install()
    errors = _open_settings(page, base_url)
    assert not page.locator('#gc-unreal-restart').is_visible()
    _flip(page)  # not saved
    monkeypatch.setattr(config_unreal, 'ENABLED', True)  # the saved value (off) now differs from the running one
    page.clock.fast_forward(16_000)
    page.wait_for_selector('#gc-unreal-restart', state='visible')
    assert page.is_checked('#gc-unreal-on') and page.is_enabled('#gc-unreal-save')
    assert errors == []
