# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Plumbing for the worker -> manager ``cards`` contract tests (card selection chunk 5).

The worker side is real from ``config.json`` to the report: a real file in
a temporary data dir (``SETHLANS_WORKER_DATA_DIR``), the real
``cards.payload.build`` with its real probe seam (``cards_cache`` ->
``cards_view.probe_now`` -> ``inventory.build``). Only the OS card probe,
the cached Blender device list and the platform rule of ``inventory.build``
are faked, so no real probe runs and Blender is never started.

``worker_machine`` is the body of each module's ``machine`` fixture; ``stored_form(report)`` is what the
manager must store for a report: the report without ``request_outcome``
(not stored, FR-CM-13) and every ``raised_at`` in ``datetime.isoformat()``
form (``null`` stays ``null``). Nothing else may change.

Module name starts with an underscore so pytest does not collect it.
"""
import copy
import json
from datetime import datetime

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import choice, in_use, inventory, os_probe, payload, startup_log, store
from sethlans_worker_agent.cards import warnings as card_warnings
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.config_store import io as config_store_io
from sethlans_worker_agent.web_ui import cards_cache

# Taken at import (collection), before any fixture could replace them.
_REAL_READ_RAW = config_compute._read_raw
_REAL_PROBE_FOR_REPORT = payload._probe_for_report
assert _REAL_READ_RAW.__name__ == '_read_raw'
assert _REAL_PROBE_FOR_REPORT.__name__ == '_probe_for_report'

NVIDIA, AMD, INTEL = 0x10DE, 0x1002, 0x8086
BLENDER_VERSION = '4.5.1'
RAISED = '2026-11-19T08:00:00+00:00'  # as ``store.add_alert`` writes it
RTX, RX = 'NVIDIA GeForce RTX 4080', 'AMD Radeon RX 7900 XT'
ARC = 'Intel(R) Arc(TM) A770 Graphics'

KEY_OK = 'pci:0000:01:00.0'        # ticked, nothing wrong (an adapter_mismatch alert does not hold it)
KEY_UNTICKED = 'pci:0000:02:00.0'
KEY_CHANGED = 'pci:0000:03:00.0'   # ticked as a 7800 XT, now a 7900 XT
KEY_HELD = 'pci:0000:23:00.0'      # back after a card_lost alert nobody dismissed
KEY_BL_LOST = 'pci:0000:41:00.0'   # card_lost_blender alert open
KEY_NEW = 'pci:0000:42:00.0'       # not in known: card_new; Blender does not offer it
KEY_LOST = 'pci:0000:81:00.0'      # ticked, not in the OS list: lost (a card entry from its saved record)
KEY_GONE = 'pci:0000:c1:00.0'      # known, unticked, absent: card_gone

ALERT_IDS = {kind: letter * 32 for kind, letter in (
    ('card_lost', 'a'), ('card_changed', 'b'), ('card_lost_blender', 'c'), ('adapter_mismatch', 'd'))}

__all__ = ['worker_machine']


def os_card(key, name, vendor_id, device_id):
    return OsCard(key=key, name=name, vendor_id=vendor_id, device_id=device_id, revision_id=0xA1)


def blender_record(key, name, backend):
    """A Blender device record as the detection cache lists it (the PCI address ends the id)."""
    address = key[len('pci:'):-len('.0')]
    prefix = 'CUDA' if backend == 'OPTIX' else backend
    suffix = '_OptiX' if backend == 'OPTIX' else ''
    return {'id': f'{prefix}_{name}_{address}{suffix}', 'name': name, 'type': backend}


def record(key, name, vendor_id=None, device_id=None):
    """A ticked card's saved record, as ``store.save`` writes it."""
    return {'key': key, 'name': name, 'vendor_id': vendor_id, 'device_id': device_id, 'revision_id': None}


def alert(kind, key, name, detail='missing from the card list', alert_id=None, raised_at=RAISED):
    return {'id': alert_id or ALERT_IDS[kind], 'kind': kind, 'key': key, 'name': name, 'detail': detail,
            'raised_at': raised_at}


# One machine with a card in every state the worker sends with a working OS probe.
MACHINE_OS = (
    os_card(KEY_OK, RTX, NVIDIA, 0x2704), os_card(KEY_UNTICKED, RTX, NVIDIA, 0x2704),
    os_card(KEY_CHANGED, RX, AMD, 0x744C), os_card(KEY_HELD, RX, AMD, 0x744C),
    os_card(KEY_BL_LOST, ARC, INTEL, 0x56A0), os_card(KEY_NEW, 'Matrox G200eW3', 0x102B, 0x0536),
)
MACHINE_BLENDER = [blender_record(KEY_OK, RTX, 'OPTIX'), blender_record(KEY_UNTICKED, RTX, 'OPTIX'),
                   blender_record(KEY_CHANGED, RX, 'HIP'), blender_record(KEY_HELD, RX, 'HIP'),
                   blender_record(KEY_BL_LOST, ARC, 'ONEAPI')]


def machine_compute(**changes):
    """The saved ``compute`` section of ``MACHINE_OS``: one alert of each kind, both notice kinds."""
    compute = {
        'method': 'cpu_gpu', 'gpu_mode': 'split', 'revision': 7,
        'cards': [record(KEY_OK, RTX, NVIDIA, 0x2704), record(KEY_CHANGED, 'AMD Radeon RX 7800 XT', AMD, 0x747E),
                  record(KEY_HELD, RX, AMD, 0x744C), record(KEY_BL_LOST, ARC, INTEL, 0x56A0),
                  record(KEY_LOST, 'NVIDIA GeForce RTX 3090', NVIDIA, 0x2204)],
        'known': [KEY_OK, KEY_UNTICKED, KEY_CHANGED, KEY_HELD, KEY_BL_LOST, KEY_LOST, KEY_GONE],
        'alerts': [alert('card_lost', KEY_HELD, RX),
                   alert('card_changed', KEY_CHANGED, 'AMD Radeon RX 7800 XT',
                         f'was AMD Radeon RX 7800 XT, now {RX}'),
                   alert('card_lost_blender', KEY_BL_LOST, ARC, f'not offered by Blender {BLENDER_VERSION} any more'),
                   alert('adapter_mismatch', KEY_OK, RTX, 'Unreal chose another adapter than the ticked card')],
    }
    compute.update(changes)
    return compute


def stored_form(report):
    """What the manager must store for ``report`` (see the module docstring)."""
    expected = copy.deepcopy(report)
    del expected['request_outcome']
    for entry in expected['alerts']:
        if entry['raised_at'] is not None:
            entry['raised_at'] = datetime.fromisoformat(entry['raised_at']).isoformat()
    return expected


class Machine:
    """The faked probes and the real ``config.json`` of one worker."""

    def __init__(self, monkeypatch, data_dir):
        self._monkeypatch = monkeypatch
        self.data_dir = data_dir

    def install(self, os_cards=MACHINE_OS, blender_cards=MACHINE_BLENDER, platform='linux'):
        """The OS probe lists ``os_cards`` (``None``: it failed); Blender's cached list is ``blender_cards``."""
        listed = tuple(os_cards) if os_cards is not None else None
        self._monkeypatch.setattr(os_probe, 'probe', lambda: listed)
        self._monkeypatch.setattr(startup_log, 'cached_blender_list',
                                  lambda: (copy.deepcopy(blender_cards), BLENDER_VERSION))
        real_build = inventory.build
        self._monkeypatch.setattr(inventory, 'build', lambda os_list, blender_list, version=None, **_kw:
                                  real_build(os_list, blender_list, version, platform_name=platform))
        cards_cache.reset_for_tests()

    def seed(self, compute):
        (self.data_dir / 'config.json').write_text(json.dumps({'compute': compute}, indent=2), encoding='utf-8')

    def compute(self):
        return json.loads((self.data_dir / 'config.json').read_text(encoding='utf-8'))['compute']

    def report(self):
        """The real ``cards`` object, as it travels (through JSON)."""
        heartbeat = {}
        payload.add_to(heartbeat)
        assert 'cards' in heartbeat, 'the worker built no cards report'
        return json.loads(json.dumps(heartbeat['cards']))


def _reset_worker_state():
    in_use.reset_env_records_for_tests()
    store.reset_memory_for_tests()
    card_warnings.reset_for_tests(False)
    cards_cache.reset_for_tests()
    choice.reset_for_tests()
    rebuild.reset_for_tests()


def worker_machine(monkeypatch, tmp_path):
    """Generator for a test module's fixture (``yield from``; a fixture here would be F811 in every test).

    Yields a ``Machine`` with no probe installed yet and no ``config.json``; worker card state is reset
    around it.
    """
    for name in (config_compute.ENV_METHOD, config_compute.ENV_GPU_MODE, config_compute.ENV_CARDS,
                 config_compute.ENV_ALLOW):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / 'worker-data'
    data_dir.mkdir()
    monkeypatch.setenv('SETHLANS_WORKER_DATA_DIR', str(data_dir))
    monkeypatch.setattr(config_store_io, 'SYSTEM_CONFIG_PATH', None)
    monkeypatch.setattr(config_compute, '_read_raw', _REAL_READ_RAW)
    monkeypatch.setattr(payload, '_probe_for_report', _REAL_PROBE_FOR_REPORT)
    monkeypatch.setattr(startup_log, '_done', True)
    _reset_worker_state()
    yield Machine(monkeypatch, data_dir)
    _reset_worker_state()
