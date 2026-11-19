# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared plumbing for the graphics card page integration tests (card selection, chunk 3).

The requests go through the real worker WSGI app (setup gate, routes, Bearer
check, body cap) and the saves through the real ``cards.store`` into a real
``config.json`` in a temporary data dir (``SETHLANS_WORKER_DATA_DIR``). Only
the OS card probe, the cached Blender device list and the platform rule of
``inventory.build`` are faked; the three fake cards come from
``_card_choice_harness``.

Module name starts with an underscore so pytest does not collect it.
"""

import io
import json
import threading

from sethlans_worker_agent import config, config_compute
from sethlans_worker_agent.cards import inventory, os_probe, startup_log
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.config_store import io as config_store_io
from sethlans_worker_agent.web_ui import auth
from sethlans_worker_agent.web_ui import wsgi_app as wsgi_app_module
from sethlans_worker_agent.web_ui.setup import handlers_status

from ._card_choice_harness import _CARDS, BLENDER_LIST, KEY_A, KEY_B, KEY_C, VENDOR, saved_record

# Captured at import (test collection), before the autouse fixture replaces it with ``lambda: None``.
_REAL_READ_RAW = config_compute._read_raw
assert _REAL_READ_RAW.__name__ == '_read_raw'

PASSWORD = 'card-pages-test-pw'
SETUP_PATH = '/api/setup/worker/cards/'
CONTROL_PATH = '/api/control/cards'
ADDRESS_A, ADDRESS_B, ADDRESS_C = (key[len('pci:'):] for key in (KEY_A, KEY_B, KEY_C))
ADDRESS_ABSENT = '0000:09:00.0'  # a PCI address no fake card sits at
JOIN_TIMEOUT = 15

__all__ = ['ADDRESS_A', 'ADDRESS_B', 'ADDRESS_C', 'ADDRESS_ABSENT', 'KEY_A', 'KEY_B', 'KEY_C',
           'saved_record']


def install(monkeypatch, tmp_path):
    """Fake the probes, point the config store at a temp data dir, set the UI password.

    Returns the data dir (``config.json`` is not created until the first save).
    """
    data_dir = tmp_path / 'worker-data'
    data_dir.mkdir()
    monkeypatch.setenv('SETHLANS_WORKER_DATA_DIR', str(data_dir))
    monkeypatch.setattr(config_store_io, 'SYSTEM_CONFIG_PATH', None)
    monkeypatch.setattr(config_compute, '_read_raw', _REAL_READ_RAW)
    os_cards = tuple(OsCard(key=key, name=name, vendor_id=VENDOR, device_id=device) for key, name, device in _CARDS)
    monkeypatch.setattr(os_probe, 'probe', lambda: os_cards)
    monkeypatch.setattr(startup_log, 'cached_blender_list', lambda: ([dict(r) for r in BLENDER_LIST], '4.5.1'))
    real_build = inventory.build
    # Join as on Windows and Linux on every CI runner (macOS has its own one-card rule, D9).
    monkeypatch.setattr(inventory, 'build', lambda os_list, blender_list, version=None, **_kw:
                        real_build(os_list, blender_list, version, platform_name='linux'))
    monkeypatch.setattr(config, 'config_file_path', tmp_path / 'config.ini')
    auth.reset_cache()
    auth.set_password(PASSWORD)
    return data_dir


def reset_wizard_state():
    """Forget the in-progress wizard topology and checkpoints (module globals)."""
    with handlers_status._state_lock:
        handlers_status._current_topology = None
        handlers_status._current_checkpoints.clear()


def bind_shutdown_event():
    """Bind a fresh, unset shutdown event for ``/api/control/*``; returns it."""
    event = threading.Event()
    wsgi_app_module._shutdown_event_ref = event
    return event


def unbind_shutdown_event():
    wsgi_app_module._shutdown_event_ref = None


def bearer(password=PASSWORD):
    return {'HTTP_AUTHORIZATION': f'Bearer {password}'}


class _Recorder:
    def __init__(self):
        self.status = ''

    def __call__(self, status, headers, exc_info=None):
        self.status = status


def call(path, method='GET', body=None, headers=None, raw=None):
    """One request through the real WSGI app; ``(status line, JSON answer)``."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b'')
    environ = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '', 'SERVER_NAME': 'localhost',
               'SERVER_PORT': '8080', 'wsgi.url_scheme': 'https', 'wsgi.input': io.BytesIO(data),
               'CONTENT_LENGTH': str(len(data))}
    environ.update(headers or {})
    recorder = _Recorder()
    answer = b''.join(wsgi_app_module.app(environ, recorder))
    return recorder.status, json.loads(answer)


def body(**changes):
    """A valid save: GPU on cards A and C; shown A, C and an address with no card (not B)."""
    request = {'method': 'gpu', 'gpu_mode': 'split', 'cards': [ADDRESS_C, ADDRESS_A],
               'shown': [ADDRESS_C, ADDRESS_ABSENT, ADDRESS_A], 'revision': 0}
    request.update(changes)
    return request


def config_json(data_dir):
    """The parsed ``config.json``, or ``None`` when it was never written."""
    path = data_dir / 'config.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def raw_bytes(data_dir):
    path = data_dir / 'config.json'
    return path.read_bytes() if path.exists() else None


def seed(data_dir, compute):
    """Write ``config.json`` with ``compute`` as the saved section."""
    (data_dir / 'config.json').write_text(json.dumps({'compute': compute}, indent=2, sort_keys=True),
                                          encoding='utf-8')


def alert(key, name, kind='card_lost'):
    """An open alert as ``store`` saves it (FR-CK-5)."""
    return {'id': '0123456789abcdef0123456789abcdef', 'kind': kind, 'key': key, 'name': name,
            'detail': 'missing from the card list', 'raised_at': '2026-11-19T08:00:00+00:00'}


def run_together(targets):
    """Run each callable on its own thread; return their results (an exception counts as a result)."""
    results = [None] * len(targets)

    def runner(index, target):
        try:
            results[index] = target()
        except BaseException as exc:  # recorded for the test to assert; never kills the thread loudly
            results[index] = exc

    threads = [threading.Thread(target=runner, args=(i, t), name=f'card-save-{i}', daemon=True)
               for i, t in enumerate(targets)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT)
        assert not thread.is_alive(), f'{thread.name} did not finish within {JOIN_TIMEOUT} s'
    return results
