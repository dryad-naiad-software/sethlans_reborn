# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``linux-drm`` backend's AMD path (FR-GL-1, FR-GL-2) on a fake sysfs tree in ``tmp_path``, with real
symlinks and the real card probe. Made-up cards and sizes, built to the kernel's documented ``amdgpu`` files
(decimal byte counts).

Skipped on Windows only: basenames such as ``0000:03:00.0`` cannot be created on NTFS. The same rules run on
every OS in ``test_linux_backend.py``.
"""
import os
import sys

import pytest

from sethlans_worker_agent.gpu_memory import linux_backend

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32', reason='sysfs basenames such as 0000:03:00.0 contain colons, which NTFS does not allow')

MIB = 1024 * 1024


def _device(root, address, files, parent=None):
    directory = (parent or root / 'devices' / 'pci0000:00') / address
    directory.mkdir(parents=True)
    base = {'class': '0x030000', 'vendor': '0x1002', 'device': '0x1234'}
    for name, content in {**base, **files}.items():
        (directory / name).write_bytes(content if isinstance(content, bytes) else content.encode() + b'\n')
    bus = root / 'bus' / 'pci' / 'devices'
    bus.mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.relpath(directory, bus), bus / address)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(linux_backend, '_logged', set())
    (tmp_path / 'sys' / 'devices').mkdir(parents=True)
    return tmp_path / 'sys'


def _vram(total_mib, used_mib):
    return {'mem_info_vram_total': str(total_mib * MIB), 'mem_info_vram_used': str(used_mib * MIB)}


def test_amd_cards_read_through_the_real_probe(root):
    _device(root, '0000:23:00.0', _vram(20464, 300))
    _device(root, '0000:03:00.0', {**_vram(24560, 1000), 'product_name': 'Test Card A'})
    backend = linux_backend.LinuxDrmBackend(sys_root=str(root))
    assert backend.open() is True
    reading = backend.sample(frozenset())
    assert reading.source == 'amdgpu-sysfs'
    assert [(gpu.index, gpu.card_key, gpu.name, gpu.total_mb, gpu.used_mb) for gpu in reading.gpus] == [
        (0, 'pci:0000:03:00.0', 'Test Card A', 24560, 1000), (1, 'pci:0000:23:00.0', 'AMD GPU 1002:1234', 20464, 300)]


def test_memory_files_are_read_with_a_4_kib_cap(root):
    _device(root, '0000:03:00.0', {'mem_info_vram_total': b'1' * 5000, 'mem_info_vram_used': b'0'})
    assert linux_backend.LinuxDrmBackend(sys_root=str(root)).open() is False


def test_a_device_outside_sys_devices_is_never_read(root, tmp_path):
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    _device(root, '0000:03:00.0', _vram(24560, 1000), parent=outside)
    assert linux_backend.LinuxDrmBackend(sys_root=str(root)).open() is False


def test_device_dir_needs_a_directory_named_by_the_address(root):
    _device(root, '0000:03:00.0', _vram(24560, 1000))
    found = linux_backend.device_dir('pci:0000:03:00.0', str(root))
    assert found == os.path.realpath(root / 'devices' / 'pci0000:00' / '0000:03:00.0')
    assert linux_backend.device_dir('pci:0000:09:00.0', str(root)) is None  # no such entry


def test_a_file_removed_after_open_makes_the_sample_none(root):
    _device(root, '0000:03:00.0', _vram(24560, 1000))
    backend = linux_backend.LinuxDrmBackend(sys_root=str(root))
    assert backend.open() is True
    os.remove(root / 'devices' / 'pci0000:00' / '0000:03:00.0' / 'mem_info_vram_used')
    assert backend.sample(frozenset()) is None
