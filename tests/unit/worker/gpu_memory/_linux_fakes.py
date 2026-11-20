# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An in-memory sysfs and ``nvidia-smi`` for the ``linux-drm`` backend tests, so they run on every OS.

The backend's ``os`` is replaced by ``posixpath`` with a fake ``realpath`` (entries under ``/sys/bus/pci/devices``
resolve to device directories), and ``os_probe_linux.read_text`` reads the fake files. Every card, address,
name and size is made up.
"""
import posixpath
from types import SimpleNamespace

from sethlans_worker_agent.cards import os_probe_linux
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.gpu_memory import linux_backend, nvidia_smi

SYS = '/sys'
DEVICES = '/sys/devices/pci0000:00'
AMD, NVIDIA, INTEL = 0x1002, 0x10DE, 0x8086
MIB = 1024 * 1024
QUERY_GPU_FLAG = nvidia_smi.QUERY_GPU[1]
PIDS_FLAG = nvidia_smi.QUERY_PIDS[1]


def amd_files(total_mib=24560, used_mib=1000):
    return {'mem_info_vram_total': str(total_mib * MIB), 'mem_info_vram_used': str(used_mib * MIB)}


class FakeHost:
    """The probe's cards, their device directories and files, and ``nvidia-smi`` outputs by flag."""

    def __init__(self):
        self.cards = []
        self.links = {}
        self.files = {}
        self.listable = True
        self.outputs = {}
        self.calls = []
        self.probes = []

    def card(self, address, vendor, name='Test Card', files=None, parent=DEVICES):
        self.cards.append(OsCard(key='pci:' + address, name=name, vendor_id=vendor, device_id=0x1234))
        real = f'{parent}/{address}'
        self.links[f'{SYS}/bus/pci/devices/{address}'] = real
        for file_name, value in (files or {}).items():
            self.files[f'{real}/{file_name}'] = value
        return real

    def probe(self, sys_root):
        self.probes.append(sys_root)
        return tuple(self.cards) if self.listable else None

    def run(self, args):
        self.calls.append(args[1])
        return self.outputs.get(args[1])

    def realpath(self, path):
        return self.links.get(path, path)

    def read_text(self, path):
        text = self.files.get(path)
        return None if text is None else text.strip()

    def backend(self, amd_only=False):
        return linux_backend.LinuxDrmBackend(amd_only=amd_only, probe=self.probe, run=self.run, sys_root=SYS,
                                             clock=lambda: 7.0)


def make_host(mocker, monkeypatch) -> FakeHost:
    """A ``FakeHost`` installed into the backend; off Windows for ``nvidia-smi``; the log-once memory cleared."""
    fake = FakeHost()
    fake_os = SimpleNamespace(path=SimpleNamespace(join=posixpath.join, basename=posixpath.basename,
                                                   realpath=fake.realpath))
    mocker.patch.object(linux_backend, 'os', fake_os)
    mocker.patch.object(os_probe_linux, 'read_text', side_effect=fake.read_text)
    monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: False)
    monkeypatch.setattr(linux_backend, '_logged', set())
    return fake
