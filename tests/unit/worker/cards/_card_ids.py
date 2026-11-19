# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender device ids and fake records shared by the card tests.

Realistic Blender device ids: an RTX 5090 (OptiX and CUDA forms), a built-in
AMD GPU, and two identical RX 7900 XT cards whose Blender order is the reverse
of their DXGI order.
"""
from sethlans_worker_agent.cards.os_probe import OsCard

RTX_5090_OPTIX = 'CUDA_NVIDIA GeForce RTX 5090_0000:01:00_OptiX'
RTX_5090_CUDA = 'CUDA_NVIDIA GeForce RTX 5090_0000:01:00'
RADEON_IGPU = 'HIP_AMD Radeon(TM) Graphics_0000:79:00'
RX_7900_XT_03 = 'HIP_AMD Radeon RX 7900 XT_0000:03:00'
RX_7900_XT_23 = 'HIP_AMD Radeon RX 7900 XT_0000:23:00'

KEY_5090 = 'pci:0000:01:00.0'
KEY_IGPU = 'pci:0000:79:00.0'
KEY_XT_03 = 'pci:0000:03:00.0'
KEY_XT_23 = 'pci:0000:23:00.0'


def blender(device_id, backend, name):
    """One record of ``hardware_detection.get_gpu_device_details()``."""
    return {'id': device_id, 'type': backend, 'name': name, 'index': 0}


def os_card(key, name, vendor_id=None, device_id=None, dxgi_index=None, dedicated_mib=None, revision_id=None):
    return OsCard(key=key, name=name, vendor_id=vendor_id, device_id=device_id,
                  dxgi_index=dxgi_index, dedicated_mib=dedicated_mib, revision_id=revision_id)


def records(caplog, logger_name, level=None):
    """Log records from one logger, optionally of one level name."""
    return [record for record in caplog.records
            if record.name == logger_name and (level is None or record.levelname == level)]


def two_xt_os_cards():
    """Two identical cards as the Windows probe lists them, in key (= DXGI) order."""
    return (
        os_card(KEY_XT_03, 'AMD Radeon RX 7900 XT', 0x1002, 0x744C, dxgi_index=0, dedicated_mib=20464,
                revision_id=0xC8),
        os_card(KEY_XT_23, 'AMD Radeon RX 7900 XT', 0x1002, 0x744C, dxgi_index=1, dedicated_mib=20464,
                revision_id=0xCC),
    )


def two_xt_blender_cards():
    """Blender's list for those two cards: the reverse of the DXGI order."""
    return [
        blender(RX_7900_XT_23, 'HIP', 'AMD Radeon RX 7900 XT'),
        blender(RX_7900_XT_03, 'HIP', 'AMD Radeon RX 7900 XT'),
    ]
