# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The one-brand rule on the worker's two card pages in a real browser (unreal-render-per-card FR-PC-BR-4).

Chromium drives the wizard's "Graphics Cards" step (``/setup``) and the settings
page (``/``) of the real worker WSGI app (``_worker_cards_browser``). Once a card
is ticked, the boxes of cards of another brand are disabled with
"Not supported together with <brand> cards."; a card of unknown brand (D7) may be
ticked only on its own ("Not supported together with other cards."). A save of
a mixed choice (seeded the way a hand edit would leave it) reaches the real
``cards.store`` and its 400 answer (FR-PC-BR-2) is shown on the page.
"""

import pytest

from sethlans_worker_agent.cards.brand import MIXED_BRANDS_TEXT

from . import _worker_cards_browser as ui
from ._worker_cards_browser import CUDA_1, HIP_1, HIP_2, NO_FAMILY, XSS_CARD

UNKNOWN = len(ui.CARDS)
# The fake cards plus one with neither a vendor id nor a Blender backend: its brand is unknown (D7).
CARDS_WITH_UNKNOWN = ui.CARDS + (('pci:0000:44:00.0', 'Card Of Unknown Brand', None, None, None),)
OTHER_CARDS_NOTE = 'Not supported together with other cards.'
MIXED_START, MIXED_END = MIXED_BRANDS_TEXT.split('{brands}')


def _note(brand):
    return f'Not supported together with {brand} cards.'


@pytest.fixture
def wizard_ui(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path) as served:
        yield served


@pytest.fixture
def wizard_ui_unknown(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path, cards=CARDS_WITH_UNKNOWN) as served:
        yield served


@pytest.fixture
def settings_ui(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path, setup_complete=True, checkpoints=()) as served:
        yield served


@pytest.fixture
def settings_ui_unknown(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path, cards=CARDS_WITH_UNKNOWN, setup_complete=True,
                             checkpoints=()) as served:
        yield served


def _open_wizard(page, base_url):
    errors = ui.watch_page(page)
    page.goto(f'{base_url}/setup')
    page.wait_for_selector('#step-cards.active #cards-form', state='visible')
    return errors


def _open_settings(page, base_url):
    errors = ui.watch_page(page)
    page.add_init_script(f"sessionStorage.setItem('workerUIPassword', {ui.PASSWORD!r})")
    page.goto(f'{base_url}/')
    page.wait_for_selector('#gc-form', state='visible')
    return errors


def _rows(page, items_id, note_class):
    """``(enabled, shown note text)`` of every card row of ``#items_id``."""
    return page.eval_on_selector_all(f'#{items_id} label', f"""rows => rows.map(r => {{
        const note = r.querySelector('.{note_class}');
        return [!r.querySelector('input').disabled, note && note.style.display !== 'none' ? note.textContent : null];
    }})""")


def _expect(count, blocked):
    """Every row enabled without a note, except ``blocked`` ({index: note})."""
    return [[False, blocked[i]] if i in blocked else [True, None] for i in range(count)]


def _check_one_brand(page, items, note_class):
    count = len(ui.CARDS)
    assert _rows(page, items, note_class) == _expect(count, {})
    ui.card_box(page, items, HIP_1).check()
    amd = _note('AMD')
    assert _rows(page, items, note_class) == _expect(count, {CUDA_1: amd, NO_FAMILY: amd, XSS_CARD: amd})
    ui.card_box(page, items, HIP_2).check()  # the same brand stays open
    ui.card_box(page, items, HIP_1).uncheck()
    ui.card_box(page, items, HIP_2).uncheck()
    assert _rows(page, items, note_class) == _expect(count, {})
    ui.card_box(page, items, NO_FAMILY).check()  # another vendor id: "PCI <vvvv>"
    other = _note('PCI 1a03')
    assert _rows(page, items, note_class) == _expect(count, {HIP_1: other, HIP_2: other, CUDA_1: other})
    assert page.locator(f'#{items} img').count() == 0 and page.evaluate('window.__xss') is None


def test_wizard_ticking_one_brand_disables_the_other_brands(page, wizard_ui):
    errors = _open_wizard(page, wizard_ui[0])
    _check_one_brand(page, 'cards-items', 'card-brand')
    assert errors == []


def test_settings_ticking_one_brand_disables_the_other_brands(page, settings_ui):
    errors = _open_settings(page, settings_ui[0])
    _check_one_brand(page, 'gc-items', 'gc-brand')
    assert errors == []


def _check_unknown_brand(page, items, note_class):
    count = len(CARDS_WITH_UNKNOWN)
    ui.card_box(page, items, UNKNOWN).check()
    assert _rows(page, items, note_class) == _expect(count, {i: OTHER_CARDS_NOTE for i in range(UNKNOWN)})
    ui.card_box(page, items, UNKNOWN).uncheck()
    ui.card_box(page, items, CUDA_1).check()
    nvidia = _note('NVIDIA')
    assert _rows(page, items, note_class) == _expect(
        count, {HIP_1: nvidia, HIP_2: nvidia, NO_FAMILY: nvidia, XSS_CARD: nvidia, UNKNOWN: OTHER_CARDS_NOTE})


def test_a_card_of_unknown_brand_is_ticked_only_on_its_own(page, wizard_ui_unknown):
    errors = _open_wizard(page, wizard_ui_unknown[0])
    _check_unknown_brand(page, 'cards-items', 'card-brand')
    assert errors == []


def test_settings_a_card_of_unknown_brand_is_ticked_only_on_its_own(page, settings_ui_unknown):
    errors = _open_settings(page, settings_ui_unknown[0])
    _check_unknown_brand(page, 'gc-items', 'gc-brand')
    assert errors == []


def _assert_mixed_text(text):
    assert text.startswith(MIXED_START) and text.endswith(MIXED_END), text
    assert 'AMD' in text and 'NVIDIA' in text


def test_settings_shows_the_refusal_of_a_mixed_choice(page, settings_ui):
    """A hand-edited choice of two brands: the ticked boxes stay open to untick; Save shows the 400 text."""
    base_url, data_dir = settings_ui
    ui.seed_choice(data_dir, [ui.record_of(HIP_1), ui.record_of(CUDA_1)], method='cpu')
    errors = _open_settings(page, base_url)
    ticked = [i in (HIP_1, CUDA_1) for i in range(len(ui.CARDS))]
    assert ui.boxes_state(page, 'gc-items') == [[t, t] for t in ticked]
    page.click('#gc-save')
    page.wait_for_function("document.getElementById('gc-result').textContent.indexOf('Graphics cards') === 0")
    _assert_mixed_text(page.inner_text('#gc-result'))
    assert ui.compute_section(data_dir)['revision'] == 1  # nothing saved
    ui.card_box(page, 'gc-items', CUDA_1).uncheck()
    assert ui.card_box(page, 'gc-items', HIP_2).is_enabled()
    assert not ui.card_box(page, 'gc-items', CUDA_1).is_enabled()
    assert errors == []


def test_wizard_shows_the_refusal_of_a_mixed_choice(page, wizard_ui):
    base_url, data_dir = wizard_ui
    ui.seed_choice(data_dir, [ui.record_of(HIP_1), ui.record_of(CUDA_1)], method='cpu')
    errors = _open_wizard(page, base_url)
    assert ui.card_box(page, 'cards-items', HIP_1).is_checked()
    assert ui.card_box(page, 'cards-items', CUDA_1).is_checked()
    page.click('#btn-cards-next')
    page.wait_for_function("document.getElementById('err-cards').textContent.indexOf('Graphics cards') === 0")
    _assert_mixed_text(page.inner_text('#err-cards'))
    assert page.locator('#step-cards.active').count() == 1
    assert errors == []
