# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cross-platform CPU model name detection.

Provides a friendly CPU brand string (e.g., "AMD Ryzen 7 3700X 8-Core
Processor") using platform-specific methods:
- Windows: reads ProcessorNameString from the registry
- macOS: reads brand string via sysctl
- Linux: reads model name from /proc/cpuinfo
- Fallback: platform.processor() or "Unknown"
"""

import platform
import subprocess


def _get_cpu_name_windows():
    """Windows: read CPU brand from the registry."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'HARDWARE\DESCRIPTION\System\CentralProcessor\0'
        )
        name, _ = winreg.QueryValueEx(key, 'ProcessorNameString')
        winreg.CloseKey(key)
        if name and name.strip():
            return name.strip()
    except (OSError, ImportError):
        pass
    return None


def _get_cpu_name_macos():
    """macOS: read Apple Silicon brand string via sysctl."""
    try:
        result = subprocess.run(
            ['sysctl', '-n', 'machdep.cpu.brand_string'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _get_cpu_name_linux():
    """Linux: read model name from /proc/cpuinfo."""
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('model name'):
                    return line.split(':', 1)[1].strip()
    except (OSError, IndexError):
        pass
    return None


def get_cpu_name():
    """Detect the CPU model name. Falls back to 'Unknown' if unavailable."""
    system = platform.system()
    # Platform-specific detection for friendly names
    if system == 'Windows':
        win_name = _get_cpu_name_windows()
        if win_name:
            return win_name
    elif system == 'Darwin':
        mac_name = _get_cpu_name_macos()
        if mac_name:
            return mac_name
    # Linux or fallback: try /proc/cpuinfo
    linux_name = _get_cpu_name_linux()
    if linux_name:
        return linux_name
    # Last resort: platform.processor() (may return generic strings)
    name = platform.processor()
    if name:
        return name
    return "Unknown"
