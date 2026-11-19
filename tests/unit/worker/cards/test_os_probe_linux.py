# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.os_probe_linux`` (FR-CI-3) without a filesystem.

The module's ``os`` and ``read_text`` are replaced by an in-memory fake
sysfs, so the parsing rules run on every OS, including Windows where a
basename such as ``0000:01:00.0`` cannot be created. The real-tree tests are
in ``test_os_probe_linux_sysfs.py``.
"""
import posixpath
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.cards import os_probe_linux

SYS = '/sys'
DEVICES = '/sys/devices/pci0000:00'


class _FakeSysfs:
    """``/sys/bus/pci/devices/<entry>`` -> real device directory, plus its files."""

    def __init__(self):
        self.links = {}
        self.files = {}
        self.listable = True

    def device(self, address, pci_class='0x030000', vendor='0x10de', device='0x2b85', product_name=None,
               parent=DEVICES, entry=None, revision=None):
        real = f'{parent}/{address}'
        self.links[f'{SYS}/bus/pci/devices/{entry or address}'] = real
        for name, value in (('class', pci_class), ('vendor', vendor), ('device', device),
                            ('product_name', product_name), ('revision', revision)):
            if value is not None:
                self.files[f'{real}/{name}'] = value + '\n'
        return real

    def listdir(self, path):
        if not self.listable or path != f'{SYS}/bus/pci/devices':
            raise FileNotFoundError(path)
        return [posixpath.basename(link) for link in self.links]

    def realpath(self, path):
        return self.links.get(path, path)

    def read_text(self, path):
        text = self.files.get(path)
        return None if text is None else text.strip()

    def install(self, mocker):
        fake_os = SimpleNamespace(listdir=self.listdir, path=SimpleNamespace(
            join=posixpath.join, basename=posixpath.basename, realpath=self.realpath))
        mocker.patch.object(os_probe_linux, 'os', fake_os)
        mocker.patch.object(os_probe_linux, 'read_text', side_effect=self.read_text)


@pytest.fixture
def sysfs(mocker):
    fake = _FakeSysfs()
    fake.install(mocker)
    return fake


class TestProbeParsing:

    def test_display_device_without_drm_entry_is_a_card(self, sysfs):
        sysfs.device('0000:01:00.0')

        cards = os_probe_linux.probe(SYS)

        assert len(cards) == 1
        card = cards[0]
        assert card.key == 'pci:0000:01:00.0'
        assert (card.vendor_id, card.device_id) == (0x10DE, 0x2B85)
        assert card.name == 'NVIDIA GPU 10de:2b85'
        assert card.dxgi_index is None
        assert card.dedicated_mib is None

    @pytest.mark.parametrize('pci_class', ['0x030000', '0x030200', '0x038000', '030000'])
    def test_every_display_subclass_is_found(self, sysfs, pci_class):
        sysfs.device('0000:01:00.0', pci_class=pci_class)

        assert len(os_probe_linux.probe(SYS)) == 1

    @pytest.mark.parametrize('pci_class', ['0x040300', '0x020000', '0x060400', '0x000000', 'garbage', ''])
    def test_other_classes_are_skipped(self, sysfs, pci_class):
        sysfs.device('0000:01:00.1', pci_class=pci_class)

        assert os_probe_linux.probe(SYS) == ()

    def test_missing_class_file_is_skipped(self, sysfs):
        sysfs.device('0000:01:00.0', pci_class=None)

        assert os_probe_linux.probe(SYS) == ()

    def test_product_name_is_used_when_present(self, sysfs):
        sysfs.device('0000:03:00.0', vendor='0x1002', device='0x744c', product_name='AMD Radeon RX 7900 XT')

        assert os_probe_linux.probe(SYS)[0].name == 'AMD Radeon RX 7900 XT'

    @pytest.mark.parametrize('product_name', ['', '   ', '\x00\x01'])
    def test_empty_product_name_falls_back(self, sysfs, product_name):
        sysfs.device('0000:03:00.0', vendor='0x1002', device='0x744c', product_name=product_name)

        assert os_probe_linux.probe(SYS)[0].name == 'AMD GPU 1002:744c'

    @pytest.mark.parametrize('vendor, expected', [
        ('0x10de', 'NVIDIA GPU 10de:2b85'), ('0x1002', 'AMD GPU 1002:2b85'),
        ('0x8086', 'Intel GPU 8086:2b85'), ('0x1af4', 'PCI GPU 1af4:2b85'),
    ])
    def test_fallback_vendor_names(self, sysfs, vendor, expected):
        sysfs.device('0000:01:00.0', vendor=vendor)

        assert os_probe_linux.probe(SYS)[0].name == expected

    @pytest.mark.parametrize('vendor, device', [
        (None, '0x2b85'), ('0x10de', None), ('nope', '0x2b85'), ('0x10000', '0x2b85'), ('0x10de', '0x1ffff'),
        ('', '0x2b85'),
    ])
    def test_unreadable_or_out_of_range_ids_are_skipped(self, sysfs, vendor, device):
        sysfs.device('0000:01:00.0', vendor=vendor, device=device)

        assert os_probe_linux.probe(SYS) == ()

    @pytest.mark.parametrize('revision, expected', [
        ('0xc8', 0xC8), ('0x00', 0), ('0xff', 0xFF), ('c1', 0xC1),
        ('0x100', None), ('0xffffffff', None), ('garbage', None), ('', None), (None, None),
    ])
    def test_revision_from_sysfs_and_bad_or_oversized_values_are_none(self, sysfs, revision, expected):
        sysfs.device('0000:03:00.0', vendor='0x1002', device='0x744c', revision=revision)

        cards = os_probe_linux.probe(SYS)

        assert len(cards) == 1   # a bad revision never drops the card
        assert cards[0].revision_id == expected

    def test_entry_resolving_outside_sys_devices_is_skipped(self, sysfs):
        sysfs.device('0000:01:00.0', parent='/tmp/elsewhere')

        assert os_probe_linux.probe(SYS) == ()

    def test_sibling_directory_with_a_shared_prefix_is_outside(self, sysfs):
        sysfs.device('0000:01:00.0', parent='/sys/devices-evil')

        assert os_probe_linux.probe(SYS) == ()

    @pytest.mark.parametrize('basename', ['card0', '0000:01:00', '0000:01:20.0', 'pci0000:00'])
    def test_directory_not_named_by_a_pci_address_is_skipped(self, sysfs, basename):
        sysfs.device(basename, entry='0000:01:00.0')

        assert os_probe_linux.probe(SYS) == ()

    def test_two_entries_at_one_address_give_one_card(self, sysfs):
        sysfs.device('0000:01:00.0')
        sysfs.links[f'{SYS}/bus/pci/devices/alias'] = f'{DEVICES}/0000:01:00.0'

        assert [card.key for card in os_probe_linux.probe(SYS)] == ['pci:0000:01:00.0']

    def test_vmd_five_digit_domain(self, sysfs):
        sysfs.device('10000:e1:00.0', parent='/sys/devices/pci10000:e0')

        assert [card.key for card in os_probe_linux.probe(SYS)] == ['pci:10000:e1:00.0']

    def test_several_cards_one_per_address_in_sorted_entry_order(self, sysfs):
        sysfs.device('0000:23:00.0', vendor='0x1002', device='0x744c')
        sysfs.device('0000:23:00.1', pci_class='0x040300', vendor='0x1002', device='0xab30')   # its audio
        sysfs.device('0000:03:00.0', vendor='0x1002', device='0x744c')
        sysfs.device('0000:00:02.0', pci_class='0x060000', vendor='0x8086', device='0x1234')   # host bridge

        assert [card.key for card in os_probe_linux.probe(SYS)] == ['pci:0000:03:00.0', 'pci:0000:23:00.0']

    def test_unlistable_pci_directory_is_none(self, sysfs):
        sysfs.listable = False

        assert os_probe_linux.probe(SYS) is None

    def test_empty_pci_directory_is_empty_not_none(self, sysfs):
        assert os_probe_linux.probe(SYS) == ()


class TestReaders:
    """``read_text`` and ``_read_hex`` with ordinary file names (these work on NTFS too)."""

    def test_read_text_strips(self, tmp_path):
        path = tmp_path / 'vendor'
        path.write_bytes(b'0x10de\n')

        assert os_probe_linux.read_text(str(path)) == '0x10de'

    def test_read_text_is_capped_at_4_kib(self, tmp_path):
        path = tmp_path / 'product_name'
        path.write_bytes(b'a' * 4096 + b'TAIL')

        assert os_probe_linux.read_text(str(path)) == 'a' * 4096

    def test_read_text_replaces_invalid_utf8(self, tmp_path):
        path = tmp_path / 'product_name'
        path.write_bytes(b'Radeon \xff')

        assert os_probe_linux.read_text(str(path)) == 'Radeon �'

    def test_read_text_of_a_missing_file_is_none(self, tmp_path):
        assert os_probe_linux.read_text(str(tmp_path / 'missing')) is None

    @pytest.mark.parametrize('content, expected', [
        (b'0x030000\n', 0x030000), (b'10de', 0x10DE), (b'', None), (b'  \n', None), (b'0xZZ', None),
    ])
    def test_read_hex(self, tmp_path, content, expected):
        path = tmp_path / 'class'
        path.write_bytes(content)

        assert os_probe_linux._read_hex(str(path)) == expected
