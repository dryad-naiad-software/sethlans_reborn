# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A fake machine for the card check tests (card selection chunk 4, FR-CK-1..3, FR-CL-4).

``CardEnv`` fakes everything the check reads: the saved ``compute`` section
(``config_compute._read_raw``, with ``config_store.set_many`` applying the
store's writes), the OS probe, the startup Blender cache and the uncached
Blender detection. Its cards are those of ``executors._fakes.fake_cards``, so
a profile from ``_fakes.profile`` matches what the check computes while no
card changed. Tests change ``present`` (OS list), ``fresh`` (detection) or
the ``fail_*`` flags.

Module name starts with an underscore so pytest does not collect it.
"""
import copy

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.capacity import card_check
from sethlans_worker_agent.cards import inventory, os_probe, startup_log
from sethlans_worker_agent.cards.os_probe import OsCard
from tests.unit.worker.executors._fakes import fake_cards, key

VENDOR = 0x10DE


def device_id(index):
    return 0x2700 + index


class CardEnv:
    """The fake machine; ``count`` cards, ``ticked`` indices saved for method ``cpu_gpu``."""

    def __init__(self, monkeypatch, count=2, ticked=None, method='cpu_gpu', gpu_mode='split'):
        self.cards = fake_cards(count)
        ticked = range(count) if ticked is None else ticked
        self.present = {card.key for card in self.cards}
        self.fresh = 'present'           # 'present': the present cards; None: the detection fails; or a list
        self.fail_probe = False
        self.fail_read = False
        self.fail_write = None           # an exception set_many raises
        self.writes = []
        self.probes = 0
        self.detections = 0
        self.compute = {'method': method, 'gpu_mode': gpu_mode, 'revision': 1,
                        'cards': [self.record(i) for i in ticked], 'known': sorted(self.present)}
        monkeypatch.setattr(config_compute, '_read_raw', self._read_raw)
        monkeypatch.setattr(config_store, 'set_many', self._set_many)
        monkeypatch.setattr(os_probe, 'probe', self._probe)
        monkeypatch.setattr(startup_log, 'cached_blender_list', lambda: (self.blender_records(), '4.5.1'))
        monkeypatch.setattr(card_check, 'detect_physical_gpus_now', self._detect)
        real_build = inventory.build
        monkeypatch.setattr(inventory, 'build', lambda os_list, blender_list, version=None, **_kw:
                            real_build(os_list, blender_list, version, platform_name='linux'))

    def record(self, index):
        card = self.cards[index]
        return {'key': card.key, 'vendor_id': VENDOR, 'device_id': device_id(index), 'revision_id': None,
                'name': card.name}

    def blender_records(self, keys=None):
        keys = self.present if keys is None else keys
        return [{'index': i, 'id': card.blender_id, 'type': card.backend, 'name': card.name}
                for i, card in enumerate(self.cards) if card.key in keys]

    def _read_raw(self):
        if self.fail_read:
            raise config_store.ConfigReadError('could not read config.json: busy')
        return copy.deepcopy(self.compute)

    def _set_many(self, pairs, expect=None):
        pairs = list(pairs)
        self.writes.append(pairs)
        if self.fail_write is not None:
            raise self.fail_write
        for dotted, value in pairs:
            self.compute[dotted.split('.')[1]] = copy.deepcopy(value)

    def _probe(self):
        self.probes += 1
        if self.fail_probe:
            return None
        return tuple(OsCard(key=card.key, name=card.name, vendor_id=VENDOR, device_id=device_id(i))
                     for i, card in enumerate(self.cards) if card.key in self.present)

    def _detect(self):
        self.detections += 1
        if self.fresh is None:
            return None
        records = self.blender_records() if self.fresh == 'present' else self.fresh
        return [(record['id'], record['type']) for record in records]

    def lose(self, index):
        """The card disappears from the OS list and from Blender's."""
        self.present.discard(key(index))

    def alerts(self):
        return self.compute.get('alerts', [])

    def alert_keys(self):
        return sorted((alert['kind'], alert['key']) for alert in self.alerts())
