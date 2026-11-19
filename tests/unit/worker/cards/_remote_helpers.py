# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Builders for the ``cards.remote`` tests (FR-CM-3..8).

``Machine`` holds the saved ``compute`` section in a dict (``_card_pages.fake_saved``), hands
``remote._fresh_inventory`` a fixed inventory (counting the probes) and counts ``rebuild.request_rebuild``.
``request(**changes)`` is a valid manager request ticking the card at 0000:23:00.0 of the two shown.

Module name starts with an underscore so pytest does not collect it.
"""
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import remote
from tests.unit.worker._card_choice import AMD, KEY_XT_03, KEY_XT_23, inv, xt_03, xt_23
from tests.unit.worker._card_pages import fake_saved

REQUEST_ID = 'ab' * 16
LOGGER = 'sethlans_worker_agent.cards.remote'
XT_DEVICE = 0x744C
OUTCOME_KEYS = ['compute.last_request', 'compute.recent_request_ids']  # the one write of a refusal


def ticked(key=KEY_XT_23, vendor_id=AMD, device_id=XT_DEVICE):
    return {'key': key, 'vendor_id': vendor_id, 'device_id': device_id}


def request(**changes):
    value = {'id': REQUEST_ID, 'method': 'gpu', 'gpu_mode': 'split', 'cards': [ticked()],
             'shown': [KEY_XT_03, KEY_XT_23], 'base_revision': 0, 'requested_by': 'user id=7',
             'requested_at': '2026-11-19T10:00:00+00:00'}
    value.update(changes)
    return value


def answer(value):
    """A heartbeat answer carrying ``value`` as ``card_choice_request``."""
    return {'id': 3, 'hostname': 'node', 'card_choice_request': value}


class Machine:
    """The saved section, the probe count and the rebuild count of one test."""

    def __init__(self, monkeypatch, compute=None, inventory=None):
        self.state, self.writes = fake_saved(monkeypatch, compute if compute is not None else {'revision': 0})
        self.inventory = inventory if inventory is not None else inv(xt_03(), xt_23())
        self.probes = 0
        self.rebuilds = 0

        def fresh_inventory():
            self.probes += 1
            return self.inventory

        def request_rebuild():
            self.rebuilds += 1

        monkeypatch.setattr(remote, '_fresh_inventory', fresh_inventory)
        monkeypatch.setattr(rebuild, 'request_rebuild', request_rebuild)

    @property
    def compute(self):
        return self.state['compute']

    @property
    def last_request(self):
        return self.compute.get('last_request')

    def written_keys(self):
        """The dotted keys of each write, in order."""
        return [[dotted for dotted, _value in pairs] for pairs in self.writes]


def messages(caplog, level=None):
    return [record.getMessage() for record in caplog.records
            if record.name == LOGGER and (level is None or record.levelname == level)]
