# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Plumbing for the worker graphics card page browser tests (card selection, chunks 3 and 4).

The worker's real WSGI app is served by the real Waitress bring-up of the
worker ``/api/health/`` integration tests (``_health_fixtures._bring_up_wsgi``),
so Chromium talks to the real routes, Bearer check, card handlers,
``cards.choice`` and ``cards.store``; saves land in a real ``config.json`` in a
temp data dir (``_card_pages_harness.install``), next to the page password
hash. Only the OS card probe and the cached Blender device list are faked,
with the card set below; no Blender and no OS probe ever run.

The worker integration suite's autouse fixtures do not reach this directory,
so ``served_worker_ui`` repeats the isolation they give (no ``SETHLANS_COMPUTE_*``
variables, no cached card probe, fresh password check state).

Module name starts with an underscore so pytest does not collect it.
"""

import contextlib
import json

from sethlans_worker_agent import config_compute, config_unreal, unreal_switch
from sethlans_worker_agent.cards import in_use, os_probe, screens, startup_log
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.web_ui import auth, cards_cache, server

from tests.integration.worker import _card_pages_harness as pages
from tests.integration.worker._health_fixtures import _bring_up_wsgi, _wait_for_bind

PASSWORD = pages.PASSWORD
BLENDER_VERSION = '4.5.1'
XSS_NAME = '<img src=x onerror="window.__xss=1">'
SETTINGS_HELP_TEXT = ("Unreal renders on a ticked card when it is switched on below. Blender uses the ticked "
                      "cards only when the method includes GPU.")

# (key, name, vendor id, device id, Blender backend or None). Shown in this order.
CARDS = (
    ('pci:0000:23:00.0', 'AMD Radeon RX 7900 XT', 0x1002, 0x744c, 'HIP'),
    ('pci:0000:24:00.0', 'AMD Radeon RX 7900 XTX', 0x1002, 0x744c, 'HIP'),
    ('pci:0000:41:00.0', 'NVIDIA GeForce RTX 4080', 0x10de, 0x2704, 'CUDA'),
    ('pci:0000:42:00.0', 'Display Adapter Without Blender', 0x1a03, 0x2000, None),
    ('pci:0000:43:00.0', XSS_NAME, 0x1a03, 0x2001, None),
)
HIP_1, HIP_2, CUDA_1, NO_FAMILY, XSS_CARD = range(len(CARDS))


def key_of(index):
    return CARDS[index][0]


def address_of(index):
    return key_of(index)[len('pci:'):]


# Screen marks (FR-CS-4): {card index: (screen, primary_screen)} and the marks each row shows.
SCREEN_MARKS = {HIP_1: (True, True), HIP_2: (True, None), CUDA_1: (None, None),
                NO_FAMILY: (False, False), XSS_CARD: (False, None)}
SHOWN_MARKS = {HIP_1: ['drives a screen', 'main screen'], HIP_2: ['drives a screen'], CUDA_1: ['screen: unknown'],
               NO_FAMILY: [], XSS_CARD: []}


def set_screen_marks(monkeypatch, marks=None):
    """Fake the platform screen read (the top-level conftest makes every mark unknown)."""
    found = {key_of(index): pair for index, pair in (SCREEN_MARKS if marks is None else marks).items()}
    monkeypatch.setattr(screens, '_platform_marks', lambda platform_name, inventory: dict(found))


def screen_warning(index, main):
    """The ``ticked_drives_screen`` text for card ``index`` (FR-CS-3)."""
    what = 'the main screen' if main else 'a screen'
    return f'{CARDS[index][1]} (PCI {address_of(index)[5:]}) is ticked and drives {what}; rendering can slow it.'


def row_marks(page, items_id, mark_class, index):
    """The screen mark texts shown on card row ``index`` of ``#items_id``."""
    return page.locator(f'#{items_id} label').nth(index).locator(f'.{mark_class}').all_inner_texts()


def boxes_state(page, items_id):
    """``(checked, enabled)`` of every card checkbox in ``#items_id``."""
    return page.eval_on_selector_all(f'#{items_id} input[type=checkbox]',
                                     'els => els.map(e => [e.checked, !e.disabled])')


def _os_cards(cards):
    return tuple(OsCard(key=key, name=name, vendor_id=vendor, device_id=device)
                 for key, name, vendor, device, _backend in cards)


def _blender_list(cards):
    """Blender's physical device records: one per card with a backend, its id ending in the PCI address."""
    return [{'index': i, 'name': name, 'type': backend, 'id': f'{backend}_{name}_{key[4:-2]}'}
            for i, (key, name, _vendor, _device, backend) in enumerate(cards) if backend]


def _isolate(monkeypatch):
    """What the worker integration conftest's autouse fixtures give, for this directory."""
    for name in (config_compute.ENV_METHOD, config_compute.ENV_GPU_MODE,
                 config_compute.ENV_CARDS, config_compute.ENV_ALLOW):
        monkeypatch.delenv(name, raising=False)
    # The Unreal switch (O14): no environment override, and the running value is off.
    monkeypatch.delenv(unreal_switch.ENV_VAR, raising=False)
    monkeypatch.setattr(config_unreal, 'ENABLED', False)
    monkeypatch.setattr(startup_log, '_done', True)
    in_use.reset_env_records_for_tests()
    cards_cache.reset_for_tests()
    auth.reset_for_tests()


def _reset():
    in_use.reset_env_records_for_tests()
    cards_cache.reset_for_tests()
    auth.reset_for_tests()
    pages.unbind_shutdown_event()


@contextlib.contextmanager
def served_worker_ui(monkeypatch, mocker, tmp_path, cards=CARDS):
    """Serve the real worker web UI on a loopback port; yields ``(base_url, data_dir)``."""
    _isolate(monkeypatch)
    data_dir = pages.install(monkeypatch, tmp_path)
    os_cards = _os_cards(cards)
    blender = _blender_list(cards)
    monkeypatch.setattr(os_probe, 'probe', lambda: os_cards)
    monkeypatch.setattr(startup_log, 'cached_blender_list', lambda: ([dict(r) for r in blender], BLENDER_VERSION))
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[])
    pages.bind_shutdown_event()
    try:
        port, _password = _bring_up_wsgi(mocker, tmp_path, password=PASSWORD)
        try:
            _wait_for_bind(port, '/api/health/', expect_status_in={200})
            yield f'http://127.0.0.1:{port}', data_dir
        finally:
            server.stop_server()
    finally:
        _reset()


def compute_section(data_dir):
    """The saved ``compute`` section of ``config.json``, or ``None`` when nothing was saved."""
    saved = pages.config_json(data_dir)
    return None if saved is None else saved.get('compute')


def ticked_keys(compute):
    return [record['key'] for record in compute['cards']]


def watch_page(page):
    """Collect page errors and console errors; returns the list (it grows while the page runs)."""
    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.on('console', lambda msg: msg.type == 'error' and 'Failed to load resource' not in msg.text
            and errors.append(msg.text))
    return errors


def record_posts(page, path):
    """``(JSON body, Authorization header)`` of each POST to ``path``; the list grows over time."""
    posts = []

    def on_request(request):
        if request.method == 'POST' and request.url.endswith(path):
            posts.append((json.loads(request.post_data), request.headers.get('authorization')))

    page.on('request', on_request)
    return posts


def card_box(page, items_id, index):
    """The checkbox of card ``index`` in the list ``#items_id``."""
    return page.locator(f'#{items_id} input[type=checkbox]').nth(index)


def record_of(index):
    """The record ``cards.store`` saves for card ``index`` (``in_use.record_of``)."""
    key, name, vendor, device, _backend = CARDS[index]
    return {'key': key, 'vendor_id': vendor, 'device_id': device, 'revision_id': None, 'name': name}


def alert_record(key, name, *, alert_id='0123456789abcdef0123456789abcdef', kind='card_lost',
                 detail='missing from the card list', raised_at='2026-11-19T08:00:00+00:00'):
    """An open alert as ``cards.store.add_alert`` saves it (FR-CK-5)."""
    return {'id': alert_id, 'kind': kind, 'key': key, 'name': name, 'detail': detail, 'raised_at': raised_at}


def seed_choice(data_dir, records=(), alerts=(), *, method='gpu', revision=1):
    """Write ``config.json`` with a saved card choice: ``records`` ticked, every fake card known."""
    pages.seed(data_dir, {'method': method, 'gpu_mode': 'split', 'cards': list(records),
                          'known': [key for key, *_rest in CARDS], 'revision': revision,
                          'alerts': list(alerts)})
