# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Builders for the card-choice tests (chunk 2: saved choice and slots).

``inv(...)`` makes an inventory from ``card(...)`` entries, ``section(...)``
the effective ``compute`` section that ticks some of them (records as
``store.save`` writes them), and ``capacity_for(...)`` the capacity the
worker builds from both, through ``matching.evaluate`` and
``card_profile.selection_of`` as ``init_capacity`` does (``select``).

Module name starts with an underscore so pytest does not collect it.
"""
from sethlans_worker_agent import config_compute
from sethlans_worker_agent.capacity import WorkerCapacity, card_profile, compute_capacity_profile
from sethlans_worker_agent.cards import in_use, matching
from sethlans_worker_agent.cards.inventory import Card, Inventory

NVIDIA, AMD = 0x10DE, 0x1002

# Blender device ids as the detection cache lists them (one id per physical card).
RTX_A_ID = 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX'
RTX_B_ID = 'CUDA_NVIDIA GeForce RTX 4080_0000:02:00_OptiX'
XT_03_ID = 'HIP_AMD Radeon RX 7900 XT_0000:03:00'
XT_23_ID = 'HIP_AMD Radeon RX 7900 XT_0000:23:00'
IGPU_ID = 'HIP_AMD Radeon(TM) Graphics_0000:79:00'

KEY_RTX_A = 'pci:0000:01:00.0'
KEY_RTX_B = 'pci:0000:02:00.0'
KEY_XT_03 = 'pci:0000:03:00.0'
KEY_XT_23 = 'pci:0000:23:00.0'
KEY_IGPU = 'pci:0000:79:00.0'


def card(key, name, blender_id=None, backend=None, vendor_id=None, device_id=None, revision_id=None, note=''):
    """One inventory card; ``blender_id`` and ``backend`` form its Blender match."""
    blender = (blender_id, backend) if blender_id else None
    return Card(key=key, name=name, vendor_id=vendor_id, device_id=device_id, revision_id=revision_id,
                blender=blender, note=note)


def rtx_a():
    return card(KEY_RTX_A, 'NVIDIA GeForce RTX 4080', RTX_A_ID, 'OPTIX', NVIDIA, 0x2704, 0xA1)


def rtx_b():
    return card(KEY_RTX_B, 'NVIDIA GeForce RTX 4080', RTX_B_ID, 'OPTIX', NVIDIA, 0x2704, 0xA1)


def xt_03(revision_id=0xC8):
    return card(KEY_XT_03, 'AMD Radeon RX 7900 XT', XT_03_ID, 'HIP', AMD, 0x744C, revision_id)


def xt_23(revision_id=0xCC):
    return card(KEY_XT_23, 'AMD Radeon RX 7900 XT', XT_23_ID, 'HIP', AMD, 0x744C, revision_id)


def igpu():
    return card(KEY_IGPU, 'AMD Radeon(TM) Graphics', IGPU_ID, 'HIP', AMD, 0x13C0, 0xC1)


def inv(*cards):
    return Inventory(cards=tuple(cards), blender_version='4.5.0')


def section(method, gpu_mode, ticked, inventory, alerts=()):
    """The effective section ticking ``ticked`` keys of ``inventory`` (records as saved)."""
    by_key = {item.key: item for item in inventory.cards}
    raw = {'method': method, 'gpu_mode': gpu_mode, 'revision': 1, 'alerts': list(alerts),
           'cards': [in_use.record_of(by_key[key]) for key in ticked]}
    return config_compute.parse(raw, {})


def detected_ids(inventory):
    """The Blender ids of every inventory card Blender offers (the detection cache)."""
    return tuple(item.blender[0] for item in inventory.cards if item.blender)


def select(sect, inventory, detected=()):
    """The cards in use, the ignored Blender ids and the problem, on the worker's path.

    ``matching.evaluate`` and ``card_profile.selection_of``, as the start
    check and the rebuild use them; ``detected`` are the Blender ids of the
    detection cache.
    """
    return card_profile.selection_of(matching.evaluate(sect, inventory), [{'id': device} for device in detected])


def capacity_for(sect, inventory, cpu_cores=16):
    """The ``WorkerCapacity`` built from ``sect`` and ``inventory`` as ``init_capacity`` builds it."""
    selection = select(sect, inventory, detected_ids(inventory))
    profile = compute_capacity_profile(
        method=sect.method, gpu_mode=sect.gpu_mode, cards=selection.cards, cpu_cores=cpu_cores,
        cpu_threads_config=0, ignored=selection.ignored, problem=selection.problem)
    return WorkerCapacity(profile)
