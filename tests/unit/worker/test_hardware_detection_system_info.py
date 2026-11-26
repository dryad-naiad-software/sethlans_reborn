# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``hardware_detection.get_system_info``: the full-registration payload (BS-WK-8)."""
from sethlans_worker_agent import hardware_detection
from sethlans_worker_agent.tool_manager import tool_manager_instance


def test_full_registration_reports_the_blender_platform(mocker):
    """BS-WK-8: full registration sends ``blender_platform`` = ``current_platform_id()``, the build the
    manager keeps for this worker."""
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[])
    for name in ('detect_gpu_devices', 'get_gpu_device_details', 'get_cpu_name'):
        mocker.patch.object(hardware_detection, name, return_value=None)
    mocker.patch.object(hardware_detection, 'current_platform_id', return_value='windows-arm64')

    info = hardware_detection.get_system_info()

    assert info['blender_platform'] == 'windows-arm64'
