# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Helpers for the graphics card page tests (FR-CW-2, FR-CW-4, FR-CW-5).

- ``fake_saved(monkeypatch)``: the saved ``compute`` section lives in a dict
  (``config_compute._read_raw`` reads it, a fake ``config_store.set_many``
  applies each write); returns ``(state, writes)``.
- ``fake_probes(monkeypatch, *inventories)``: ``cards_view.probe_now``
  returns a probe per call (the last inventory repeats); returns the list of
  probes handed out, so ``len(...)`` is the probe count.
- ``call(path, ...)``: one request through the worker's WSGI app.
- ``body(**changes)``: a valid save body ticking ``0000:23:00.0``.

Module name starts with an underscore so pytest does not collect it.
"""
import copy
import json

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.web_ui import cards_view
from sethlans_worker_agent.web_ui import wsgi_app as wsgi_app_module
from tests.unit.worker._card_choice import inv, xt_03, xt_23
from tests.unit.worker._wsgi_helpers import StartResponseRecorder, drain, make_environ

RUNTIME_GET = '/api/cards/'
RUNTIME_POST = '/api/control/cards'
HIP_LIST = [{'id': 'x', 'type': 'HIP'}]


def fake_saved(monkeypatch, compute=None):
    """The saved section in a dict; ``(state, writes)``."""
    state = {'compute': compute if compute is not None else {'revision': 0}}
    writes = []

    def set_many(pairs, expect=None):
        pairs = list(pairs)
        writes.append(pairs)
        for dotted, value in pairs:
            state['compute'][dotted.split('.')[1]] = copy.deepcopy(value)

    monkeypatch.setattr(config_compute, '_read_raw', lambda: copy.deepcopy(state['compute']))
    monkeypatch.setattr(config_store, 'set_many', set_many)
    return state, writes


def fake_probes(monkeypatch, *inventories, blender_cards=HIP_LIST):
    """``cards_view.probe_now`` hands out one probe per call; returns the probes handed out."""
    inventories = inventories or (inv(xt_03(), xt_23()),)
    handed = []

    def probe_now():
        probe = cards_view.Probe(inventories[min(len(handed), len(inventories) - 1)], blender_cards)
        handed.append(probe)
        return probe

    monkeypatch.setattr(cards_view, 'probe_now', probe_now)
    return handed


def call(path, method='GET', body_value=None, headers=None, raw=None):
    """``(status, JSON answer)`` of one request; ``raw`` bytes win over ``body_value``."""
    data = raw if raw is not None else (json.dumps(body_value).encode() if body_value is not None else b'')
    rec = StartResponseRecorder()
    answer = drain(wsgi_app_module.app(make_environ(path, method=method, body=data, headers=headers), rec))
    return rec.status_code, json.loads(answer)


def body(**changes):
    """A valid save body (method gpu, split, ticking 0000:23:00.0 of the two cards shown)."""
    value = {'method': 'gpu', 'gpu_mode': 'split', 'cards': ['0000:23:00.0'],
             'shown': ['0000:03:00.0', '0000:23:00.0'], 'revision': 0}
    value.update(changes)
    return value
