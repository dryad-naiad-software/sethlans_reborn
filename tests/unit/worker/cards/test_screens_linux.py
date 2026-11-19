# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.screens_linux`` (FR-CS-1, AC-36).

``TestFakeDrm`` replaces the module's ``os`` and ``_read_status`` with an
in-memory ``/sys/class/drm``, so the rules run on every OS (NTFS cannot hold
``0000:03:00.0``). ``TestDrmTree`` builds a real tree with symlinks in
``tmp_path`` and is skipped on Windows only.
"""
import os
import posixpath
import sys
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.cards import screens, screens_linux
from sethlans_worker_agent.cards.inventory import Card, Inventory

REAL_PLATFORM_MARKS = screens._platform_marks
REAL_LINUX_READ = screens_linux.read
SYS = '/sys'
DRM = '/sys/class/drm'
DEVICES = '/sys/devices/pci0000:00'
KEY_03, KEY_23 = 'pci:0000:03:00.0', 'pci:0000:23:00.0'


class _FakeDrm:

    def __init__(self, mocker):
        self.entries = []
        self.links = {}
        self.status = {}
        self.listable = True
        fake_os = SimpleNamespace(listdir=self.listdir, path=SimpleNamespace(
            join=posixpath.join, basename=posixpath.basename, realpath=self.realpath))
        mocker.patch.object(screens_linux, 'os', fake_os)
        mocker.patch.object(screens_linux, '_read_status', side_effect=lambda path: self.status.get(path))

    def card(self, number, address, parent=DEVICES):
        self.entries.append(f'card{number}')
        self.links[f'{DRM}/card{number}/device'] = f'{parent}/{address}'

    def connector(self, number, name, status):
        self.entries.append(f'card{number}-{name}')
        if status is not None:
            self.status[f'{DRM}/card{number}-{name}/status'] = status

    def listdir(self, path):
        if not self.listable or path != DRM:
            raise FileNotFoundError(path)
        return list(self.entries)

    def realpath(self, path):
        return self.links.get(path, path)


@pytest.fixture
def drm(mocker):
    return _FakeDrm(mocker)


class TestFakeDrm:

    def test_connected_connector_drives_a_screen_and_primary_is_none(self, drm):
        drm.card(1, '0000:03:00.0')
        drm.connector(1, 'DP-1', 'connected')
        drm.connector(1, 'HDMI-A-1', 'disconnected')

        assert screens_linux.read(SYS) == {KEY_03: (True, None)}

    def test_only_disconnected_connectors_is_no_screen(self, drm):
        drm.card(0, '0000:23:00.0')
        drm.connector(0, 'DP-2', 'disconnected')

        assert screens_linux.read(SYS) == {KEY_23: (False, None)}

    def test_drm_card_without_connectors_is_no_screen(self, drm):
        drm.card(0, '0000:23:00.0')

        assert screens_linux.read(SYS) == {KEY_23: (False, None)}

    def test_unreadable_status_is_unknown_unless_another_is_connected(self, drm):
        drm.card(0, '0000:03:00.0')
        drm.connector(0, 'DP-1', None)
        drm.card(1, '0000:23:00.0')
        drm.connector(1, 'DP-1', None)
        drm.connector(1, 'DP-2', 'connected')

        assert screens_linux.read(SYS) == {KEY_03: (None, None), KEY_23: (True, None)}

    def test_two_cards_each_by_its_own_number(self, drm):
        drm.card(0, '0000:03:00.0')
        drm.card(1, '0000:23:00.0')
        drm.connector(1, 'eDP-1', 'connected')
        drm.connector(0, 'DP-1', 'disconnected')

        assert screens_linux.read(SYS) == {KEY_03: (False, None), KEY_23: (True, None)}

    def test_device_outside_sys_devices_and_non_pci_device_are_skipped(self, drm):
        drm.card(0, '0000:03:00.0', parent='/tmp/elsewhere')
        drm.connector(0, 'DP-1', 'connected')
        drm.card(2, 'simple-framebuffer.0', parent='/sys/devices/platform')
        drm.connector(2, 'Unknown-1', 'connected')

        assert screens_linux.read(SYS) == {}

    def test_render_nodes_and_connectors_of_unknown_cards_are_ignored(self, drm):
        drm.entries += ['renderD128', 'version', 'card9-DP-1']
        drm.card(0, '0000:03:00.0')

        assert screens_linux.read(SYS) == {KEY_03: (False, None)}

    def test_card_with_no_drm_entry_reads_unknown_through_screens(self, mocker, drm):
        drm.card(0, '0000:03:00.0')
        drm.connector(0, 'DP-1', 'connected')
        mocker.patch.object(screens, '_platform_marks', REAL_PLATFORM_MARKS)
        mocker.patch.object(screens_linux, 'read', side_effect=lambda: REAL_LINUX_READ(SYS))
        inventory = Inventory(cards=(Card(key=KEY_03, name='a'), Card(key=KEY_23, name='nvidia, no nvidia-drm')))

        assert screens.read(inventory, 'linux') == {KEY_03: (True, None), KEY_23: (None, None)}

    def test_missing_drm_directory_is_none(self, drm):
        drm.listable = False

        assert screens_linux.read(SYS) is None


@pytest.mark.skipif(sys.platform == 'win32', reason='sysfs names such as 0000:03:00.0 contain colons (NTFS)')
class TestDrmTree:

    @staticmethod
    def _card(root, number, address, connectors):
        device = root / 'devices' / 'pci0000:00' / address
        device.mkdir(parents=True, exist_ok=True)
        card = root / 'class' / 'drm' / f'card{number}'
        card.mkdir(parents=True)
        os.symlink(os.path.relpath(device, card), card / 'device')
        for name, content in connectors.items():
            connector = root / 'class' / 'drm' / f'card{number}-{name}'
            connector.mkdir()
            (connector / 'status').write_bytes(content)

    def test_real_tree(self, tmp_path):
        root = tmp_path / 'sys'
        self._card(root, 0, '0000:03:00.0', {'DP-1': b'disconnected\n'})
        self._card(root, 1, '0000:23:00.0', {'DP-1': b'connected\n', 'HDMI-A-1': b'disconnected\n'})

        assert screens_linux.read(str(root)) == {KEY_03: (False, None), KEY_23: (True, None)}

    def test_status_is_read_with_a_4_kib_cap(self, tmp_path):
        root = tmp_path / 'sys'
        self._card(root, 0, '0000:03:00.0', {'DP-1': b' ' * 4096 + b'connected'})

        assert screens_linux.read(str(root)) == {KEY_03: (False, None)}
