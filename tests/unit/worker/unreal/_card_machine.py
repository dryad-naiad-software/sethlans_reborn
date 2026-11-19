# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A fake Windows machine for the Unreal card tests (card selection chunk 7).

``CardMachine`` replaces the DXGI and D3DKMT bindings (no DLL is loaded, so
the tests run on every OS), makes the OS probe and the inventory take the
Windows path, gives no Blender list (Unreal does not need one) and serves
the saved choice from ``store.snapshot``.

Module name starts with an underscore so pytest does not collect it.
"""
from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import inventory, os_probe, startup_log, store
from sethlans_worker_agent.gpu_memory import win_adapter_info, win_dxgi
from sethlans_worker_agent.gpu_memory.win_dxgi import AdapterDesc

GIB = 1024 ** 3
SOFTWARE = win_dxgi.DXGI_ADAPTER_FLAG_SOFTWARE


def adapter(index, address, gib=16, flags=0, name=None, vendor=0x1002, device=0x744C):
    """A DXGI adapter at raw index ``index``; ``address`` is what D3DKMT reports for it (``None``: virtual)."""
    desc = AdapterDesc(index, name or f'Card {index}', vendor, device, gib * GIB, (0, 1000 + index), flags, 0xC8)
    return desc, address


def software(index):
    return adapter(index, None, gib=0, flags=SOFTWARE, name='Microsoft Basic Render Driver', vendor=0x1414,
                   device=0x8C)


def key(address):
    return f'pci:{address}'


class CardMachine:

    def __init__(self, monkeypatch, pairs, ticked=(), method='cpu', alerts=()):
        self.pairs = list(pairs)
        self.dxgi_ok = True
        self.read_failed = False
        self.probes = 0
        self.method, self.alerts = method, tuple(alerts)
        self.ticked = list(ticked)
        self.saved = {address: self.record(address) for address in self.ticked}  # as saved before any change
        real_probe, real_build = os_probe.probe, inventory.build
        monkeypatch.setattr(win_dxgi, 'enumerate_adapters', self._enumerate)
        monkeypatch.setattr(win_adapter_info, 'available', lambda: True)
        monkeypatch.setattr(win_adapter_info, 'pci_address', self._address)
        monkeypatch.setattr(os_probe, 'probe', lambda platform_name=None: self._count(real_probe))
        monkeypatch.setattr(inventory, 'build', lambda os_list, blender, version=None, **_kw:
                            real_build(os_list, blender, version, platform_name='win32'))
        monkeypatch.setattr(startup_log, 'cached_blender_list', lambda: (None, None))
        monkeypatch.setattr(store, 'snapshot', self.section)

    def _count(self, real_probe):
        self.probes += 1
        return real_probe(platform_name='win32')

    def _enumerate(self):
        return tuple(desc for desc, _address in self.pairs) if self.dxgi_ok else None

    def _address(self, luid):
        return {desc.luid: address for desc, address in self.pairs}[luid]

    def record(self, address):
        """The saved record of the card at ``address`` (a card no longer listed keeps its old record)."""
        desc = next((desc for desc, found in self.pairs if found == address), None)
        if desc is None:
            return {'key': key(address), 'vendor_id': 0x1002, 'device_id': 0x744C, 'revision_id': 0xC8, 'name': 'Gone'}
        return {'key': key(address), 'vendor_id': desc.vendor_id, 'device_id': desc.device_id,
                'revision_id': desc.revision, 'name': desc.name}

    def section(self):
        if self.read_failed:
            return config_compute.ComputeSection(read_failed=True)
        cards = tuple(self.saved.get(address) or self.record(address) for address in self.ticked)
        return config_compute.ComputeSection(method=self.method, cards=cards, alerts=self.alerts)

    def inventory(self):
        return inventory.build(os_probe.probe(), None)
