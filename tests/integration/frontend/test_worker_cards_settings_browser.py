# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker settings page "Graphics Cards" section in a real browser (card selection, chunks 3 and 4).

Chromium drives ``/`` of the real worker WSGI app after setup
(``_worker_cards_browser``): ``GET /api/cards/``, the Bearer-checked
``POST /api/control/cards`` and ``POST /api/control/cards/dismiss`` are the
real handlers; saves and dismissals write the real ``config.json``, and seeded
choices and alerts are written there the way ``cards.store`` saves them. Only
the OS card probe and the cached Blender list are fake.
"""

import pytest

from sethlans_worker_agent.capacity import rebuild

from . import _worker_cards_browser as ui
from ._worker_cards_browser import CUDA_1, HIP_1, HIP_2, NO_FAMILY, XSS_CARD

CONTROL_CARDS = '/api/control/cards'
DISMISS = '/api/control/cards/dismiss'
SAVED_TEXT = "Saved. The new graphics card choice is in use once running GPU jobs on removed cards finish."
DISMISSED_TEXT = "Dismissed. A card that is still missing or changed is alerted again at the next check."
REBUILD_TEXT = "Waiting for running GPU jobs to finish before using the new card list."
NONE_USABLE_TEXT = ("None of the ticked graphics cards can be used (missing, changed or waiting for a person to "
                    "check them). This worker takes no GPU jobs until one can.")
STALE_TEXT = "The card choice was changed meanwhile (on this page or from the manager). Reload the page."
HELD_LABEL = 'held until its alert is dismissed'
XSS_DETAIL = '<img src=x onerror="window.__xss=2">'
LOST_RECORD = {'key': 'pci:0000:51:00.0', 'vendor_id': 0x1002, 'device_id': 0x73bf, 'revision_id': None,
               'name': 'AMD Radeon RX 6800'}
# Records whether the Dismiss button was disabled when the page sent the dismissal.
WATCH_DISMISS = """(() => { const real = window.fetch; window.fetch = function(url) {
  if (String(url).endsWith('/cards/dismiss')) {
    window.__disabledAtPost = document.querySelector('#gc-alert-list button').disabled;
  }
  return real.apply(this, arguments); }; })()"""


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


def _row_text(page, index):
    return page.locator('#gc-items label').nth(index).inner_text()


def _wait_result(page, start):
    page.wait_for_function(f"document.getElementById('gc-result').textContent.indexOf({start!r}) === 0")


def test_settings_save_with_bearer_password_writes_config_json(page, worker_ui):
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url, ui.PASSWORD)
    posts = ui.record_posts(page, CONTROL_CARDS)
    # Nothing saved yet, so every card is new (FR-CK-4); the notices go away with the save (D20).
    notices = page.locator('#gc-notice-list li')
    assert notices.count() == len(ui.CARDS)
    assert notices.first.inner_text() == f'New card found: {ui.CARDS[HIP_1][1]}'
    page.select_option('#gc-method', 'gpu')
    assert page.is_disabled('#gc-save')
    _tick(page, HIP_1, HIP_2)
    assert page.locator('#gc-combine-row').is_visible()
    page.check('#gc-combine')
    page.click('#gc-save')
    _wait_result(page, 'Saved')
    assert page.inner_text('#gc-result') == SAVED_TEXT
    assert posts[0][1] == f'Bearer {ui.PASSWORD}'
    compute = ui.compute_section(data_dir)
    assert (compute['method'], compute['gpu_mode'], compute['revision']) == ('gpu', 'combined', 1)
    assert ui.ticked_keys(compute) == [ui.key_of(HIP_1), ui.key_of(HIP_2)]
    # The page reloads the saved choice; a second save goes on from the new revision.
    page.wait_for_function("document.getElementById('gc-save').disabled === false")
    assert ui.card_box(page, 'gc-items', HIP_1).is_checked() and page.is_checked('#gc-combine')
    assert not page.locator('#gc-notices').is_visible()
    assert errors == []


def test_settings_cpu_shows_cards_and_saves_the_ticks(page, worker_ui):
    """C-23 on the settings page."""
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url, ui.PASSWORD)
    assert page.input_value('#gc-method') == 'cpu'
    assert page.locator('#gc-list').is_visible() and page.inner_text('#gc-help') == ui.SETTINGS_HELP_TEXT
    assert page.is_enabled('#gc-save')
    _tick(page, NO_FAMILY, XSS_CARD)
    assert not page.locator('#gc-combine-row').is_visible()
    page.click('#gc-save')
    _wait_result(page, 'Saved')
    compute = ui.compute_section(data_dir)
    assert (compute['method'], compute['gpu_mode']) == ('cpu', 'split')
    assert ui.ticked_keys(compute) == [ui.key_of(NO_FAMILY), ui.key_of(XSS_CARD)]
    assert errors == []


def test_settings_wrong_password_saves_nothing(page, worker_ui):
    base_url, data_dir = worker_ui
    errors = _open_settings(page, base_url, 'not-the-password')
    _tick(page, CUDA_1)
    page.click('#gc-save')
    _wait_result(page, 'Wrong')
    assert ui.compute_section(data_dir) is None
    assert page.evaluate("sessionStorage.getItem('workerUIPassword')") is None
    assert errors == []


def test_settings_card_and_alert_texts_with_markup_are_shown_as_text(page, worker_ui):
    base_url, data_dir = worker_ui
    ui.seed_choice(data_dir, alerts=[ui.alert_record(ui.key_of(XSS_CARD), ui.XSS_NAME, detail=XSS_DETAIL)],
                   method='cpu')
    errors = _open_settings(page, base_url, ui.PASSWORD)
    assert ui.XSS_NAME in _row_text(page, XSS_CARD)
    alert_text = page.locator('#gc-alert-list li').first.inner_text()
    assert ui.XSS_NAME in alert_text and XSS_DETAIL in alert_text
    assert page.locator('#gc-items img').count() == 0 and page.locator('#gc-alerts img').count() == 0
    assert page.evaluate('window.__xss') is None
    assert errors == []


def test_settings_open_alert_is_shown_and_dismiss_removes_it(page, worker_ui):
    base_url, data_dir = worker_ui
    name = ui.CARDS[HIP_1][1]
    alert = ui.alert_record(ui.key_of(HIP_1), name, kind='card_changed', detail='was RX 7900 XT, now RX 7800 XT')
    ui.seed_choice(data_dir, [ui.record_of(HIP_1)], [alert])
    page.add_init_script(WATCH_DISMISS)
    errors = _open_settings(page, base_url, ui.PASSWORD)
    posts = ui.record_posts(page, DISMISS)
    rows = page.locator('#gc-alert-list li')
    assert rows.count() == 1
    raised = page.evaluate(f"new Date({alert['raised_at']!r}).toLocaleString()")
    text = rows.first.inner_text()
    assert name in text and alert['detail'] in text and f'raised {raised}' in text
    # The held card stays ticked and is marked; no ticked card can be used, so the problem line shows.
    assert ui.card_box(page, 'gc-items', HIP_1).is_checked() and HELD_LABEL in _row_text(page, HIP_1)
    assert page.locator('#gc-items label').nth(HIP_1).evaluate("el => el.classList.contains('gc-bad')")
    assert page.inner_text('#gc-problem') == NONE_USABLE_TEXT
    page.click('#gc-alert-list button')
    _wait_result(page, 'Dismissed')
    assert page.inner_text('#gc-result') == DISMISSED_TEXT
    page.wait_for_selector('#gc-alerts', state='hidden')
    assert page.evaluate('window.__disabledAtPost') is True
    assert posts == [({'alert_id': alert['id']}, f'Bearer {ui.PASSWORD}')]
    assert ui.compute_section(data_dir)['alerts'] == []
    assert HELD_LABEL not in _row_text(page, HIP_1) and not page.locator('#gc-problem').is_visible()
    assert ui.card_box(page, 'gc-items', HIP_1).is_checked()
    assert errors == []


def test_settings_lost_ticked_card_is_listed_but_never_sent(page, worker_ui):
    base_url, data_dir = worker_ui
    ui.seed_choice(data_dir, [LOST_RECORD, ui.record_of(CUDA_1)])
    errors = _open_settings(page, base_url, ui.PASSWORD)
    posts = ui.record_posts(page, CONTROL_CARDS)
    missing = page.locator('#gc-missing .gc-opt')
    assert missing.count() == 1
    text = missing.first.inner_text()
    assert 'Ticked' in text and f"{LOST_RECORD['name']} (PCI 51:00.0)" in text and 'missing' in text
    assert page.locator('#gc-missing input').count() == 0
    assert missing.first.evaluate("el => el.classList.contains('gc-bad')")
    assert ui.card_box(page, 'gc-items', CUDA_1).is_checked()
    page.click('#gc-save')
    _wait_result(page, 'Saved')
    sent = posts[0][0]
    assert sent['cards'] == [ui.address_of(CUDA_1)] and '0000:51:00.0' not in sent['shown']
    page.wait_for_function("document.querySelectorAll('#gc-missing .gc-opt').length === 0")
    assert ui.ticked_keys(ui.compute_section(data_dir)) == [ui.key_of(CUDA_1)]
    assert errors == []


def test_settings_shows_screen_marks_and_one_warning_per_ticked_screen_card(page, worker_ui, monkeypatch):
    """FR-CS-4 / FR-CS-3: marks next to each card; the ticked-card warning is listed once; no tick changes."""
    base_url, data_dir = worker_ui
    ui.set_screen_marks(monkeypatch)
    ui.seed_choice(data_dir, [ui.record_of(HIP_1), ui.record_of(HIP_2)])
    errors = _open_settings(page, base_url, ui.PASSWORD)
    for index, shown in ui.SHOWN_MARKS.items():
        assert ui.row_marks(page, 'gc-items', 'gc-screen', index) == shown
    warnings = page.locator('#gc-warnings li').all_inner_texts()
    for index, main in ((HIP_1, True), (HIP_2, False)):
        text = ui.screen_warning(index, main)
        assert warnings.count(text) == 1
        assert text not in page.inner_text('#gc-items')
    # The AMD cards are ticked; the other brands' boxes are disabled (FR-PC-BR-4).
    ticked = [i in (HIP_1, HIP_2) for i in range(len(ui.CARDS))]
    assert ui.boxes_state(page, 'gc-items') == [[t, t] for t in ticked]
    assert errors == []


def test_settings_says_when_the_new_card_list_waits(page, worker_ui, monkeypatch):
    base_url, _data_dir = worker_ui
    monkeypatch.setattr(rebuild, 'rebuild_waiting', lambda: True)
    errors = _open_settings(page, base_url, ui.PASSWORD)
    assert page.locator('#gc-rebuild').is_visible() and page.inner_text('#gc-rebuild') == REBUILD_TEXT
    assert errors == []


def test_settings_slow_poll_refreshes_alerts_but_keeps_unsaved_choices(page, worker_ui, monkeypatch):
    base_url, data_dir = worker_ui
    page.clock.install()
    errors = _open_settings(page, base_url, ui.PASSWORD)
    assert not page.locator('#gc-alerts').is_visible() and not page.locator('#gc-rebuild').is_visible()
    page.select_option('#gc-method', 'gpu')
    _tick(page, HIP_1)
    # Meanwhile the saved choice moves on (as from the manager), an alert opens and a rebuild waits.
    ui.seed_choice(data_dir, [ui.record_of(CUDA_1)], [ui.alert_record(ui.key_of(HIP_2), ui.CARDS[HIP_2][1])],
                   method='cpu', revision=4)
    monkeypatch.setattr(rebuild, 'rebuild_waiting', lambda: True)
    page.clock.fast_forward(16_000)
    page.wait_for_selector('#gc-alerts', state='visible')
    assert page.locator('#gc-rebuild').is_visible()
    assert page.input_value('#gc-method') == 'gpu'
    assert ui.card_box(page, 'gc-items', HIP_1).is_checked()
    assert not ui.card_box(page, 'gc-items', CUDA_1).is_checked()
    # The save is still based on the revision the person saw, so it is refused instead of overwriting.
    posts = ui.record_posts(page, CONTROL_CARDS)
    page.click('#gc-save')
    _wait_result(page, 'The card choice')
    assert page.inner_text('#gc-result') == STALE_TEXT and posts[0][0]['revision'] == 0
    assert ui.compute_section(data_dir)['revision'] == 4
    assert errors == []
