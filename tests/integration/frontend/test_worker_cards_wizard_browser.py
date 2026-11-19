# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker setup wizard "Graphics Cards" step in a real browser (card selection, chunk 3).

Chromium drives ``/setup`` of the real worker WSGI app (``_worker_cards_browser``):
the GET and POST of ``/api/setup/worker/cards/``, the wizard checkpoints and
``/api/setup/verify/`` are the real handlers, and a save writes the real
``config.json``. Only the OS card probe and the cached Blender list are fake.

Covers AC-2 (Next needs a tick under GPU; "Combine GPUs" only for two ticked
cards of one family), C-23 (cards and help line under CPU; CPU saves the
ticks), the wizard save, the #264 resume names, literal card names and the
no-card page.
"""

import sys

import pytest

from sethlans_worker_agent.cards import os_probe
from sethlans_worker_agent.web_ui.setup.handlers_status import append_wizard_checkpoint, get_current_checkpoints

from tests.integration.worker import _card_pages_harness as pages

from . import _worker_cards_browser as ui
from ._worker_cards_browser import CUDA_1, HIP_1, HIP_2, NO_FAMILY, XSS_CARD

SETUP_CARDS = '/api/setup/worker/cards/'
PROBE_FAILED_TEXT = "The operating system could not list the graphics cards; the cards below come from Blender."


@pytest.fixture
def worker_ui(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path) as served:
        yield served


@pytest.fixture
def worker_ui_no_cards(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path, cards=()) as served:
        yield served


def _open_cards_step(page, base_url):
    errors = ui.watch_page(page)
    page.goto(f'{base_url}/setup')
    page.wait_for_selector('#step-cards.active #cards-form', state='visible')
    return errors


def _tick(page, *indexes, on=True):
    for index in indexes:
        ui.card_box(page, 'cards-items', index).set_checked(on)


def _combine_shown(page):
    return page.locator('#cards-combine-row').is_visible()


def _next_enabled(page):
    return page.locator('#btn-cards-next').is_enabled()


def test_gpu_needs_a_ticked_card_and_combine_needs_two_of_one_family(page, worker_ui):
    """AC-2."""
    base_url, data_dir = worker_ui
    errors = _open_cards_step(page, base_url)
    assert page.input_value('#cards-method') == 'cpu'
    page.select_option('#cards-method', 'gpu')
    assert not _next_enabled(page)
    _tick(page, HIP_1)
    assert _next_enabled(page)
    assert not _combine_shown(page)  # one ticked card
    _tick(page, HIP_2)
    assert _combine_shown(page)  # two HIP cards
    page.check('#cards-combine')
    page.select_option('#cards-method', 'cpu')
    assert not _combine_shown(page) and not page.is_checked('#cards-combine')
    page.select_option('#cards-method', 'cpu_gpu')
    assert _combine_shown(page)
    _tick(page, HIP_1, HIP_2, on=False)
    assert not _next_enabled(page)  # CPU and GPU also needs a card
    _tick(page, CUDA_1, NO_FAMILY)
    assert _next_enabled(page)
    assert not _combine_shown(page)  # a CUDA card and a card Blender does not drive
    _tick(page, HIP_1)
    assert not _combine_shown(page)  # CUDA and HIP are two families
    assert ui.compute_section(data_dir) is None  # ticking saves nothing
    assert errors == []


def test_cpu_shows_cards_and_help_and_saves_the_ticks(page, worker_ui):
    """C-23: under CPU the cards stay, Next needs no tick, and the save keeps the ticks for Unreal."""
    base_url, data_dir = worker_ui
    errors = _open_cards_step(page, base_url)
    assert page.input_value('#cards-method') == 'cpu'
    assert page.locator('#cards-list').is_visible()
    assert page.locator('#cards-items input[type=checkbox]').count() == len(ui.CARDS)
    assert page.inner_text('#cards-help') == ui.WIZARD_HELP_TEXT
    assert page.locator('#cards-help').is_visible()
    assert _next_enabled(page)
    _tick(page, CUDA_1, NO_FAMILY)
    assert not _combine_shown(page)
    page.click('#btn-cards-next')
    page.wait_for_selector('#step-verify.active')
    compute = ui.compute_section(data_dir)
    assert (compute['method'], compute['gpu_mode']) == ('cpu', 'split')
    assert ui.ticked_keys(compute) == [ui.key_of(CUDA_1), ui.key_of(NO_FAMILY)]
    assert errors == []


def test_wizard_save_writes_config_json_and_moves_to_verify(page, worker_ui):
    base_url, data_dir = worker_ui
    errors = _open_cards_step(page, base_url)
    posts = ui.record_posts(page, SETUP_CARDS)
    page.select_option('#cards-method', 'gpu')
    _tick(page, HIP_2, HIP_1)
    page.check('#cards-combine')
    page.click('#btn-cards-next')
    page.wait_for_selector('#step-verify.active')
    page.wait_for_selector('#check-list', state='visible')  # the real verify ran
    assert posts[0][0] == {'method': 'gpu', 'gpu_mode': 'combined',
                           'cards': [ui.address_of(HIP_1), ui.address_of(HIP_2)],
                           'shown': [ui.address_of(i) for i in range(len(ui.CARDS))], 'revision': 0}
    compute = ui.compute_section(data_dir)
    assert (compute['method'], compute['gpu_mode'], compute['revision']) == ('gpu', 'combined', 1)
    assert ui.ticked_keys(compute) == [ui.key_of(HIP_1), ui.key_of(HIP_2)]
    assert sorted(compute['known']) == sorted(ui.key_of(i) for i in range(len(ui.CARDS)))
    assert 'cards_chosen' in get_current_checkpoints()
    assert errors == []


def test_resume_after_blender_opens_the_cards_step(page, worker_ui):
    """#264: checkpoint ``blender_installed`` resumes on the cards step."""
    base_url, _data_dir = worker_ui
    errors = _open_cards_step(page, base_url)
    assert not page.locator('#step-blender').is_visible()
    assert errors == []


def test_resume_after_cards_chosen_opens_verify(page, worker_ui):
    """#264: checkpoint ``cards_chosen`` resumes on verify, without listing the cards."""
    base_url, _data_dir = worker_ui
    append_wizard_checkpoint('cards_chosen')
    errors = ui.watch_page(page)
    card_gets = []
    page.on('request', lambda request: request.url.endswith(SETUP_CARDS) and card_gets.append(request.method))
    page.goto(f'{base_url}/setup')
    page.wait_for_selector('#step-verify.active')
    page.wait_for_selector('#check-list', state='visible')
    assert not page.locator('#step-cards').is_visible()
    assert card_gets == []
    assert errors == []


def test_card_name_with_markup_is_shown_as_text(page, worker_ui):
    base_url, _data_dir = worker_ui
    errors = _open_cards_step(page, base_url)
    row = page.locator('#cards-items label').nth(XSS_CARD)
    assert ui.XSS_NAME in row.inner_text()
    assert page.locator('#cards-items img').count() == 0
    assert page.evaluate('window.__xss') is None
    assert errors == []


def test_no_card_offers_only_cpu(page, worker_ui_no_cards):
    base_url, data_dir = worker_ui_no_cards
    errors = _open_cards_step(page, base_url)
    assert page.locator('#cards-none').is_visible()
    assert page.inner_text('#cards-none') == ui.NO_CARD_TEXT
    assert page.input_value('#cards-method') == 'cpu'
    disabled = page.evaluate("[...document.querySelectorAll('#cards-method option')]"
                             ".filter(o => o.disabled).map(o => o.value)")
    assert disabled == ['gpu', 'cpu_gpu']
    assert not page.locator('#cards-list').is_visible()
    assert _next_enabled(page)
    page.click('#btn-cards-next')
    page.wait_for_selector('#step-verify.active')
    compute = ui.compute_section(data_dir)
    assert (compute['method'], compute['cards']) == ('cpu', [])
    assert errors == []


@pytest.mark.skipif(sys.platform == 'darwin', reason="macOS offers no card when the OS probe fails (D9)")
def test_os_probe_failed_offers_blender_cards_and_keeps_saved_gpu(page, worker_ui, monkeypatch):
    """FR-CI-6: without the OS list the cards come from Blender; GPU stays selectable and the saved method stays."""
    base_url, data_dir = worker_ui
    monkeypatch.setattr(os_probe, 'probe', lambda: None)
    pages.seed(data_dir, {'method': 'gpu', 'revision': 2})
    errors = _open_cards_step(page, base_url)
    assert page.locator('#cards-probe-failed').is_visible()
    assert page.inner_text('#cards-probe-failed') == PROBE_FAILED_TEXT
    assert page.input_value('#cards-method') == 'gpu'
    disabled = page.evaluate("[...document.querySelectorAll('#cards-method option')]"
                             ".filter(o => o.disabled).map(o => o.value)")
    assert disabled == []
    assert not page.locator('#cards-none').is_visible()
    # Only the cards Blender drives come back; they can be ticked and saved.
    assert page.locator('#cards-items input[type=checkbox]').count() == 3
    assert not _next_enabled(page)
    _tick(page, HIP_1)
    page.click('#btn-cards-next')
    page.wait_for_selector('#step-verify.active')
    compute = ui.compute_section(data_dir)
    assert (compute['method'], compute['revision']) == ('gpu', 3)
    assert ui.ticked_keys(compute) == [ui.key_of(HIP_1)]
    assert errors == []


def test_wizard_shows_problem_and_non_ok_states_and_ignores_missing(page, worker_ui):
    """Chunk 4: a resumed wizard on a machine with a held and a missing card (no alerts or Dismiss here)."""
    base_url, data_dir = worker_ui
    lost = {'key': 'pci:0000:51:00.0', 'vendor_id': 0x1002, 'device_id': 0x73bf, 'revision_id': None,
            'name': 'AMD Radeon RX 6800'}
    ui.seed_choice(data_dir, [ui.record_of(HIP_1), lost],
                   [ui.alert_record(ui.key_of(HIP_1), ui.CARDS[HIP_1][1], kind='card_changed')])
    errors = _open_cards_step(page, base_url)
    rows = page.locator('#cards-items label')
    assert 'held until its alert is dismissed' in rows.nth(HIP_1).inner_text()
    assert rows.nth(HIP_1).locator('.card-state.bad').count() == 1
    assert rows.nth(HIP_2).locator('.card-state').count() == 0  # unticked: no label
    assert page.locator('#cards-items input[type=checkbox]').count() == len(ui.CARDS)
    assert 'AMD Radeon RX 6800' not in page.inner_text('#cards-form')
    assert page.inner_text('#cards-problem').startswith('None of the ticked graphics cards can be used')
    assert page.locator('#step-cards button', has_text='Dismiss').count() == 0
    assert errors == []
