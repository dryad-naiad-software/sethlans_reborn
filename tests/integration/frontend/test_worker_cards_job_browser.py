# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker settings page: the render holding each graphics card (unreal-render-per-card FR-PC-MGR-4).

Chromium drives ``/`` of the real worker WSGI app after setup
(``_worker_cards_browser``). ``GET /api/cards/`` is the real handler; the
test rewrites its answer in the browser to give each card the ``job_id`` and
``renderer`` pair (both null when the card is free), so the page is checked
for held Blender, held Unreal and free cards without running a render.
"""

import json

import pytest

from . import _worker_cards_browser as ui
from ._worker_cards_browser import CUDA_1, HIP_1, HIP_2, NO_FAMILY, XSS_CARD

FREE = (None, None)


@pytest.fixture
def worker_ui(monkeypatch, mocker, tmp_path):
    with ui.served_worker_ui(monkeypatch, mocker, tmp_path) as served:
        yield served


def _hold_cards(page, holds):
    """Give card ``index`` the pair ``holds[index]`` in every ``/api/cards/`` answer; others are free.

    ``holds`` is a dict the test may change later; the next GET uses the new pairs.
    """
    def handler(route):
        response = route.fetch()
        data = response.json()
        for index, card in enumerate(data['cards']):
            card['job_id'], card['renderer'] = holds.get(index, FREE)
        route.fulfill(response=response, body=json.dumps(data))

    page.route('**/api/cards/', handler)


def _open_settings(page, base_url):
    errors = ui.watch_page(page)
    page.add_init_script(f"sessionStorage.setItem('workerUIPassword', {ui.PASSWORD!r})")
    page.goto(f'{base_url}/')
    page.wait_for_selector('#gc-form', state='visible')
    return errors


def _job_texts(page):
    """The job line of each card row, or ``None`` for a row without one."""
    return page.evaluate("""Array.from(document.querySelectorAll('#gc-items label')).map(function(row) {
      var job = row.querySelector('.gc-job'); return job ? job.textContent : null; })""")


def test_settings_shows_the_render_holding_each_card(page, worker_ui):
    base_url, _data_dir = worker_ui
    _hold_cards(page, {
        HIP_1: (42, 'BLENDER'),
        CUDA_1: (7, 'UNREAL'),
        HIP_2: (9, None),  # half a pair is not shown
        NO_FAMILY: (3, 'toString'),  # an unknown renderer, even an Object property name: not shown
        XSS_CARD: ('<b>1</b>', 'BLENDER'),  # not a job id: not shown, never parsed as HTML
    })
    errors = _open_settings(page, base_url)
    texts = _job_texts(page)
    assert texts[HIP_1] == 'Rendering job 42 (Blender)'
    assert texts[CUDA_1] == 'Rendering job 7 (Unreal)'
    assert texts[HIP_2] is None and texts[NO_FAMILY] is None and texts[XSS_CARD] is None
    assert len(texts) == len(ui.CARDS)
    assert page.locator('#gc-items b').count() == 0
    assert errors == []


def test_settings_free_cards_show_no_job(page, worker_ui):
    base_url, _data_dir = worker_ui
    _hold_cards(page, {})
    errors = _open_settings(page, base_url)
    assert _job_texts(page) == [None] * len(ui.CARDS)
    assert errors == []


def test_settings_slow_poll_updates_the_job_line(page, worker_ui):
    base_url, _data_dir = worker_ui
    holds = {}
    _hold_cards(page, holds)
    page.clock.install()
    errors = _open_settings(page, base_url)
    assert _job_texts(page)[HIP_2] is None
    holds[HIP_2] = (15, 'UNREAL')
    page.clock.fast_forward(16000)
    page.wait_for_function("document.querySelectorAll('#gc-items .gc-job').length === 1")
    assert _job_texts(page)[HIP_2] == 'Rendering job 15 (Unreal)'
    holds.clear()  # the render ended: the card is free again
    page.clock.fast_forward(16000)
    page.wait_for_function("document.querySelectorAll('#gc-items .gc-job').length === 0")
    assert errors == []
