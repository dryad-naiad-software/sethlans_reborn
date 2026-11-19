# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker settings page "Graphics Cards" section in a real browser (card selection, chunk 3).

Chromium drives ``/`` of the real worker WSGI app after setup
(``_worker_cards_browser``): ``GET /api/cards/`` and the Bearer-checked
``POST /api/control/cards`` are the real handlers and a save writes the real
``config.json``. Only the OS card probe and the cached Blender list are fake.
"""

import pytest

from . import _worker_cards_browser as ui
from ._worker_cards_browser import CUDA_1, HIP_1, HIP_2, NO_FAMILY, XSS_CARD

CONTROL_CARDS = '/api/control/cards'
SAVED_TEXT = "Saved. The new graphics card choice takes effect when the worker restarts."


@pytest.fixture
def worker_ui(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path, setup_complete=True, checkpoints=()) as served:
        yield served


def _open_settings(page, base_url, password):
    errors = ui.watch_page(page)
    page.add_init_script(f"sessionStorage.setItem('workerUIPassword', {password!r})")
    page.goto(f'{base_url}/')
    page.wait_for_selector('#gc-form', state='visible')
    return errors


def _tick(page, *indexes):
    for index in indexes:
        ui.card_box(page, 'gc-items', index).check()


def test_settings_save_with_bearer_password_writes_config_json(page, worker_ui):
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url, ui.PASSWORD)
    posts = ui.record_posts(page, CONTROL_CARDS)
    page.select_option('#gc-method', 'gpu')
    assert page.is_disabled('#gc-save')
    _tick(page, HIP_1, HIP_2)
    assert page.locator('#gc-combine-row').is_visible()
    page.check('#gc-combine')
    page.click('#gc-save')
    page.wait_for_function("document.getElementById('gc-result').textContent.indexOf('Saved') === 0")
    assert page.inner_text('#gc-result') == SAVED_TEXT
    assert posts[0][1] == f'Bearer {ui.PASSWORD}'
    compute = ui.compute_section(data_dir)
    assert (compute['method'], compute['gpu_mode'], compute['revision']) == ('gpu', 'combined', 1)
    assert ui.ticked_keys(compute) == [ui.key_of(HIP_1), ui.key_of(HIP_2)]
    # The page reloads the saved choice; a second save goes on from the new revision.
    page.wait_for_function("document.getElementById('gc-save').disabled === false")
    assert ui.card_box(page, 'gc-items', HIP_1).is_checked() and page.is_checked('#gc-combine')
    assert errors == []


def test_settings_cpu_shows_cards_and_saves_the_ticks(page, worker_ui):
    """C-23 on the settings page."""
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url, ui.PASSWORD)
    assert page.input_value('#gc-method') == 'cpu'
    assert page.locator('#gc-list').is_visible() and page.inner_text('#gc-help') == ui.HELP_TEXT
    assert page.is_enabled('#gc-save')
    _tick(page, CUDA_1, NO_FAMILY)
    assert not page.locator('#gc-combine-row').is_visible()
    page.click('#gc-save')
    page.wait_for_function("document.getElementById('gc-result').textContent.indexOf('Saved') === 0")
    compute = ui.compute_section(data_dir)
    assert (compute['method'], compute['gpu_mode']) == ('cpu', 'split')
    assert ui.ticked_keys(compute) == [ui.key_of(CUDA_1), ui.key_of(NO_FAMILY)]
    assert errors == []


def test_settings_wrong_password_saves_nothing(page, worker_ui):
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url, 'not-the-password')
    _tick(page, CUDA_1)
    page.click('#gc-save')
    page.wait_for_function("document.getElementById('gc-result').textContent.indexOf('Wrong') === 0")
    assert ui.compute_section(data_dir) is None
    assert page.evaluate("sessionStorage.getItem('workerUIPassword')") is None
    assert errors == []


def test_settings_card_name_with_markup_is_shown_as_text(page, worker_ui):
    base_url, _data_dir = worker_ui
    errors = _open_settings(page, base_url, ui.PASSWORD)
    assert ui.XSS_NAME in page.locator('#gc-items label').nth(XSS_CARD).inner_text()
    assert page.locator('#gc-items img').count() == 0
    assert page.evaluate('window.__xss') is None
    assert errors == []
