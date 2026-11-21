# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared builders for the card-choice tests (worker card selection
FR-CM-9..15): a worker whose stored report has two cards, the action's
body and the worker's reported outcome.
"""

from tests.unit.manager.card_report_helpers import KEY, KEY_2, card, stored
from workers.models import Worker

HB = '/api/heartbeat/'
NVIDIA = {'vendor_id': 0x10DE, 'device_id': 0x2B85}
NVIDIA_2 = {'vendor_id': 0x10DE, 'device_id': 0x2684}
AMD = {'vendor_id': 0x1002, 'device_id': 0x164E}
SECOND_CARD = card(key=KEY_2, name='AMD Radeon(TM) Graphics', ticked=False, backend='HIP', **AMD)
# A second card of the first card's brand, for two ticked cards (Unreal render per card FR-PC-BR-5).
SECOND_NVIDIA = card(key=KEY_2, name='NVIDIA GeForce RTX 4090', ticked=False, backend='OPTIX', **NVIDIA_2)


def url(pk):
    return f'{HB}{pk}/card-choice/'


def two_cards(**overrides):
    """The stored report: KEY ticked (NVIDIA, OptiX), KEY_2 unticked (AMD, HIP), no alerts, revision 3."""
    values = {'cards': [card(), SECOND_CARD], 'alerts': [], 'notices': [], 'warnings': []}
    values.update(overrides)
    return stored(**values)


def body(**overrides):
    """A valid action body ticking KEY, based on revision 3."""
    values = {
        'method': 'gpu', 'gpu_mode': 'split', 'cards': [{'key': KEY, **NVIDIA}],
        'shown': [KEY, KEY_2], 'base_revision': 3,
    }
    values.update(overrides)
    return values


def outcome(request_id, **overrides):
    """A worker's ``cards.request_outcome`` for ``request_id`` (FR-CM-7 without requested_by/_at)."""
    values = {
        'id': request_id, 'result': 'applied', 'code': 'applied', 'reason': '',
        'revision': 4, 'finished_at': '2026-11-19T12:00:05+00:00',
    }
    values.update(overrides)
    return values


def reported(make_worker_client, hostname, **status):
    """A worker with the two-card report stored: ``(worker, worker client)``."""
    worker, client, _token = make_worker_client(hostname)
    Worker.objects.filter(pk=worker.pk).update(card_status=two_cards(**status))
    return worker, client


def row(worker):
    return Worker.objects.get(pk=worker.pk)
