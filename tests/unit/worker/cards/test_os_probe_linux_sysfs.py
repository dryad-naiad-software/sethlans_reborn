# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.os_probe_linux`` (FR-CI-3) on a fake sysfs tree in
``tmp_path``, with real symlinks as in ``/sys/bus/pci/devices``.

Skipped on Windows only: basenames such as ``0000:01:00.0`` cannot be created
on NTFS. They run on the Linux and macOS CI runners. The parsing rules are
also covered without a filesystem in ``test_os_probe_linux.py``.
"""
import os
import sys

import pytest

from sethlans_worker_agent.cards import os_probe, os_probe_linux

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32', reason='sysfs basenames such as 0000:01:00.0 contain colons, which NTFS does not allow')


class _Tree:
    def __init__(self, root):
        self.root = root
        self.devices = root / 'devices' / 'pci0000:00'
        self.bus = root / 'bus' / 'pci' / 'devices'
        self.devices.mkdir(parents=True)
        self.bus.mkdir(parents=True)

    def device(self, address, files, parent=None, link_name=None):
        directory = (parent or self.devices) / address
        directory.mkdir(parents=True)
        for name, content in files.items():
            (directory / name).write_bytes(content if isinstance(content, bytes) else content.encode() + b'\n')
        os.symlink(os.path.relpath(directory, self.bus), self.bus / (link_name or address))
        return directory

    def probe(self):
        return os_probe_linux.probe(sys_root=str(self.root))


def _gpu(vendor='0x10de', device='0x2b85', pci_class='0x030000', **extra):
    return {'class': pci_class, 'vendor': vendor, 'device': device, **extra}


@pytest.fixture
def tree(tmp_path):
    return _Tree(tmp_path / 'sys')


class TestSysfsTree:

    def test_class_0x03_device_with_no_drm_entry_is_found(self, tree):
        tree.device('0000:01:00.0', _gpu())

        cards = tree.probe()

        assert [(card.key, card.name, card.vendor_id, card.device_id) for card in cards] == [
            ('pci:0000:01:00.0', 'NVIDIA GPU 10de:2b85', 0x10DE, 0x2B85)]

    def test_revision_file_is_read_and_oversized_value_is_none(self, tree):
        tree.device('0000:03:00.0', _gpu(vendor='0x1002', device='0x744c', revision='0xc8'))
        tree.device('0000:23:00.0', _gpu(vendor='0x1002', device='0x744c', revision='0x1c8'))
        tree.device('0000:79:00.0', _gpu(vendor='0x1002', device='0x15bf'))

        assert [(card.key, card.revision_id) for card in tree.probe()] == [
            ('pci:0000:03:00.0', 0xC8), ('pci:0000:23:00.0', None), ('pci:0000:79:00.0', None)]

    def test_non_display_devices_are_skipped(self, tree):
        tree.device('0000:01:00.0', _gpu(product_name='NVIDIA GeForce RTX 5090'))
        tree.device('0000:01:00.1', _gpu(pci_class='0x040300', device='0x22e8'))
        tree.device('0000:00:01.0', _gpu(pci_class='0x060400', vendor='0x1022', device='0x14db'))

        assert [(card.key, card.name) for card in tree.probe()] == [('pci:0000:01:00.0', 'NVIDIA GeForce RTX 5090')]

    def test_two_identical_cards_each_get_their_address(self, tree):
        files = _gpu(vendor='0x1002', device='0x744c', product_name='AMD Radeon RX 7900 XT')
        tree.device('0000:23:00.0', files)
        tree.device('0000:03:00.0', files)

        assert [card.key for card in tree.probe()] == ['pci:0000:03:00.0', 'pci:0000:23:00.0']

    def test_link_to_a_directory_outside_sys_devices_is_skipped(self, tree, tmp_path):
        outside = tmp_path / 'elsewhere'
        outside.mkdir()
        tree.device('0000:01:00.0', _gpu(), parent=outside)

        assert tree.probe() == ()

    def test_link_name_does_not_decide_the_key(self, tree):
        tree.device('card0', _gpu(), link_name='0000:01:00.0')

        assert tree.probe() == ()

    def test_two_links_to_one_device_give_one_card(self, tree):
        directory = tree.device('0000:01:00.0', _gpu())
        os.symlink(os.path.relpath(directory, tree.bus), tree.bus / 'alias')

        assert [card.key for card in tree.probe()] == ['pci:0000:01:00.0']

    def test_nested_bridge_path_and_vmd_domain(self, tree):
        bridge = tree.root / 'devices' / 'pci10000:e0' / '10000:e0:01.0'
        bridge.mkdir(parents=True)
        tree.device('10000:e1:00.0', _gpu(vendor='0x8086', device='0x56a0'), parent=bridge)

        assert [(card.key, card.name) for card in tree.probe()] == [('pci:10000:e1:00.0', 'Intel GPU 8086:56a0')]

    def test_files_are_read_with_a_4_kib_cap(self, tree):
        tree.device('0000:01:00.0', _gpu(pci_class=b' ' * 4096 + b'0x030000'))
        tree.device('0000:02:00.0', _gpu(product_name=b'X' * 5000))

        cards = tree.probe()

        assert [card.key for card in cards] == ['pci:0000:02:00.0']
        assert cards[0].name == 'X' * 128

    def test_missing_vendor_file_is_skipped(self, tree):
        files = _gpu()
        del files['vendor']
        tree.device('0000:01:00.0', files)

        assert tree.probe() == ()

    def test_broken_link_is_skipped(self, tree):
        os.symlink('../../../devices/pci0000:00/0000:09:00.0', tree.bus / '0000:09:00.0')
        tree.device('0000:01:00.0', _gpu())

        assert [card.key for card in tree.probe()] == ['pci:0000:01:00.0']

    def test_missing_pci_directory_is_none(self, tmp_path):
        assert os_probe_linux.probe(sys_root=str(tmp_path / 'nothing')) is None

    def test_empty_pci_directory_is_empty(self, tree):
        assert tree.probe() == ()

    def test_through_os_probe_with_the_tree(self, mocker, tree):
        tree.device('0000:23:00.0', _gpu(vendor='0x1002', device='0x744c'))
        tree.device('0000:03:00.0', _gpu(vendor='0x1002', device='0x744c'))
        real_probe = os_probe_linux.probe
        mocker.patch.object(os_probe_linux, 'probe', side_effect=lambda: real_probe(sys_root=str(tree.root)))

        assert [card.key for card in os_probe.probe(platform_name='linux')] == [
            'pci:0000:03:00.0', 'pci:0000:23:00.0']
