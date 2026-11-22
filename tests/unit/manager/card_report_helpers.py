# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Builders of a valid worker ``cards`` report (worker card selection
FR-CR-1), shared by the manager card tests.
"""

KEY = 'pci:0000:01:00.0'
KEY_2 = 'pci:0000:79:00.0'
ALERT_ID = 'a' * 32
ALERT_ID_2 = 'b' * 32
RAISED_AT = '2026-11-19T10:00:00+00:00'


def card(**overrides):
    """One valid card entry."""
    entry = {
        'key': KEY, 'name': 'NVIDIA GeForce RTX 5090', 'vendor_id': 0x10DE,
        'device_id': 0x2B85, 'revision_id': 0xA1, 'ticked': True, 'state': 'ok', 'blender': True,
        'backend': 'OPTIX', 'note': '', 'screen': False, 'primary_screen': None,
        'job_id': None, 'renderer': None,
    }
    entry.update(overrides)
    return entry


def alert(**overrides):
    """One valid alert record (FR-CK-5)."""
    entry = {
        'id': ALERT_ID, 'kind': 'card_lost', 'key': KEY, 'name': 'RTX 5090',
        'detail': 'missing from the card list', 'raised_at': RAISED_AT,
    }
    entry.update(overrides)
    return entry


def notice(**overrides):
    entry = {'kind': 'card_new', 'key': KEY_2, 'name': 'AMD Radeon RX 7900 XT'}
    entry.update(overrides)
    return entry


def warning(**overrides):
    entry = {'code': 'ticked_drives_screen', 'key': KEY, 'text': 'RTX 5090 drives a screen.'}
    entry.update(overrides)
    return entry


def report(**overrides):
    """A valid report with one entry in each list."""
    body = {
        'schema': 1, 'method': 'gpu', 'gpu_mode': 'split', 'env_override': False,
        'revision': 3, 'manager_changes_allowed': True, 'rebuild_waiting': False,
        'problem': '', 'cards': [card()], 'alerts': [alert()],
        'notices': [notice()], 'warnings': [warning()], 'request_outcome': None,
    }
    body.update(overrides)
    return body


def stored(**overrides):
    """What the manager stores for ``report(**overrides)`` (no ``request_outcome``)."""
    body = report(**overrides)
    body.pop('request_outcome', None)
    return body
