# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Linux ``linux-drm`` GPU memory backend (FR-GL-1..6, FR-GN-3) on a fake sysfs and ``nvidia-smi``."""
import logging

import pytest

from sethlans_worker_agent.gpu_memory import linux_backend, nvidia_smi
from sethlans_worker_agent.gpu_memory import reader as reader_module
from sethlans_worker_agent.gpu_memory.types import make_gpu
from tests.unit.worker.gpu_memory._linux_fakes import (AMD, INTEL, MIB, NVIDIA, PIDS_FLAG, QUERY_GPU_FLAG,
                                                       amd_files, make_host)
from tests.unit.worker.gpu_memory._nvidia_fixtures import BUS_B, BUSY, gpu_block, listing

LOG = 'sethlans_worker_agent.gpu_memory.linux_backend'
# nvidia-smi numbers the cards its own way (here the reverse of PCI order): the reader never uses that number.
TWO_ROWS = ("0, 00000000:02:00.0, 16303, 200, Test Card B\n"
            "1, 00000000:01:00.0, 16303, 1500, Test Card A\n")


@pytest.fixture
def host(mocker, monkeypatch):
    return make_host(mocker, monkeypatch)


def opened(host, **kwargs):
    backend = host.backend(**kwargs)
    assert backend.open() is True
    return backend


class TestAmd:

    def test_read_from_the_device_directory(self, host):
        host.card('0000:23:00.0', AMD, 'Test Card B', amd_files(20464, 300))
        host.card('0000:03:00.0', AMD, 'Test Card A', amd_files(24560, 1000))
        reading = opened(host).sample(frozenset({1}))
        assert reading.source == 'amdgpu-sysfs' and reading.taken_at == 7.0
        assert reading.measures_card is True and reading.measures_process is False  # OQ-7: AMD never attributed
        assert reading.gpus == (make_gpu(0, 'Test Card A', 24560, 1000, None, 'pci:0000:03:00.0'),
                                make_gpu(1, 'Test Card B', 20464, 300, None, 'pci:0000:23:00.0'))
        assert host.calls == []  # no nvidia-smi on an AMD-only host

    @pytest.mark.parametrize('files', [
        {}, {'mem_info_vram_total': str(8 * 1024 * MIB)}, {'mem_info_vram_used': '0'},
        {'mem_info_vram_total': 'lots', 'mem_info_vram_used': '0'},
        {'mem_info_vram_total': '-8589934592', 'mem_info_vram_used': '0'},
        {'mem_info_vram_total': '0x200000000', 'mem_info_vram_used': '0'},
        {'mem_info_vram_total': str(MIB - 1), 'mem_info_vram_used': '0'}])
    def test_unreadable_files_make_the_card_unreadable(self, host, files):
        host.card('0000:03:00.0', AMD, files=files)
        assert host.backend().open() is False

    def test_used_is_clamped_to_the_total_and_rounded_down(self, host):
        host.card('0000:03:00.0', AMD, files={'mem_info_vram_total': str(4 * MIB + 5),
                                              'mem_info_vram_used': str(9 * MIB)})
        [gpu] = opened(host).sample(frozenset()).gpus
        assert (gpu.total_mb, gpu.used_mb) == (4, 4)

    @pytest.mark.parametrize('parent', ['/tmp/elsewhere', '/sys/devices-evil'])
    def test_a_device_directory_outside_sys_devices_is_never_read(self, host, parent):
        host.card('0000:03:00.0', AMD, files=amd_files(), parent=parent)
        assert host.backend().open() is False

    def test_a_link_to_a_directory_of_another_address_is_never_read(self, host):
        host.card('0000:03:00.0', AMD, files=amd_files())
        host.links['/sys/bus/pci/devices/0000:03:00.0'] = '/sys/devices/pci0000:00/0000:04:00.0'
        host.files.update({f'/sys/devices/pci0000:00/0000:04:00.0/{name}': value
                           for name, value in amd_files().items()})
        assert host.backend().open() is False


class TestNvidia:

    def test_matched_by_card_key_index_by_pci_rank(self, host):
        host.card('0000:02:00.0', NVIDIA, 'NVIDIA GPU 10de:1234')
        host.card('0000:01:00.0', NVIDIA, 'NVIDIA GPU 10de:1234')
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS, PIDS_FLAG: BUSY}
        reading = opened(host).sample(frozenset({4100, 4300}))
        assert reading.source == 'nvidia-smi' and reading.measures_process is True
        assert reading.gpus == (make_gpu(0, 'Test Card A', 16303, 1500, 600, 'pci:0000:01:00.0'),
                                make_gpu(1, 'Test Card B', 16303, 200, 50, 'pci:0000:02:00.0'))

    def test_one_card_query_and_one_listing_per_sample(self, host):
        host.card('0000:01:00.0', NVIDIA)
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS, PIDS_FLAG: BUSY}
        backend = opened(host)
        host.calls.clear()
        backend.sample(frozenset())
        assert host.calls == [QUERY_GPU_FLAG, PIDS_FLAG]

    def test_an_unmatched_card_is_unreadable_and_logged_once(self, host, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        host.card('0000:01:00.0', NVIDIA, 'Test Card A')
        host.card('0000:05:00.0', NVIDIA, 'Test Card E')
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS}
        reading = opened(host).sample(frozenset())
        assert [gpu.card_key for gpu in reading.gpus] == ['pci:0000:01:00.0']
        assert [gpu.index for gpu in reading.gpus] == [0]
        host.backend().open()
        assert caplog.messages.count(
            "GPU memory: card pci:0000:05:00.0 'Test Card E' is not read: nvidia-smi does not list it.") == 1

    def test_an_address_listed_twice_by_the_card_query_is_unreadable(self, host):
        host.card('0000:01:00.0', NVIDIA)
        host.card('0000:02:00.0', NVIDIA)
        twice = TWO_ROWS + "2, 00000000:01:00.0, 8192, 7000, Test Card X\n"
        host.outputs = {QUERY_GPU_FLAG: twice}
        reading = opened(host).sample(frozenset())
        assert [gpu.card_key for gpu in reading.gpus] == ['pci:0000:02:00.0']

    def test_an_address_listed_twice_later_makes_the_sample_none(self, host):
        host.card('0000:01:00.0', NVIDIA)
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS}
        backend = opened(host)
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS + "2, 00000000:01:00.0, 8192, 7000, Test Card X\n"}
        assert backend.sample(frozenset()) is None

    def test_without_nvidia_smi_no_card_is_readable(self, host):
        host.card('0000:01:00.0', NVIDIA)
        assert host.backend().open() is False

    def test_an_empty_nvidia_smi_name_falls_back_to_the_probe_name(self, host):
        host.card('0000:01:00.0', NVIDIA, 'NVIDIA GPU 10de:1234')
        host.outputs = {QUERY_GPU_FLAG: "0, 00000000:01:00.0, 8192, 10, [N/A]\n"}
        assert opened(host).sample(frozenset()).gpus[0].name == 'NVIDIA GPU 10de:1234'

    def test_a_card_missing_from_the_listing_is_not_attributed(self, host):
        host.card('0000:01:00.0', NVIDIA)
        host.card('0000:02:00.0', NVIDIA)
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS, PIDS_FLAG: listing(gpu_block(BUS_B))}
        reading = opened(host).sample(frozenset({4300}))
        assert [gpu.sethlans_used_mb for gpu in reading.gpus] == [None, 0]
        assert reading.measures_process is False


class TestOtherVendorsAndMixed:

    def test_intel_is_unreadable_logged_once_and_still_counts_for_the_rank(self, host, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        host.card('0000:00:02.0', INTEL, 'Test Card I')
        host.card('0000:01:00.0', NVIDIA)
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS}
        reading = opened(host).sample(frozenset())
        assert [(gpu.index, gpu.card_key) for gpu in reading.gpus] == [(1, 'pci:0000:01:00.0')]
        host.backend().open()
        assert caplog.messages.count(
            "GPU memory: card pci:0000:00:02.0 'Test Card I' is not read: no GPU memory reader for vendor 0x8086.") == 1

    def test_only_unreadable_cards_fail_open(self, host):
        host.card('0000:00:02.0', INTEL)
        assert host.backend().open() is False and host.calls == []

    def test_amd_and_nvidia_give_mixed(self, host):
        host.card('0000:03:00.0', AMD, 'Test Card C', amd_files(24560, 1000))
        host.card('0000:01:00.0', NVIDIA)
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS, PIDS_FLAG: BUSY}
        reading = opened(host).sample(frozenset({4100}))
        assert reading.source == 'mixed' and reading.measures_process is False
        assert [(gpu.index, gpu.sethlans_used_mb) for gpu in reading.gpus] == [(0, 600), (1, None)]

    def test_amd_only_skips_nvidia(self, host, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        host.card('0000:03:00.0', AMD, 'Test Card C', amd_files())
        host.card('0000:01:00.0', NVIDIA, 'Test Card A')
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS}
        backend = opened(host, amd_only=True)
        reading = backend.sample(frozenset())
        assert backend.name == 'amdgpu-sysfs' and reading.source == 'amdgpu-sysfs'
        assert [(gpu.index, gpu.card_key) for gpu in reading.gpus] == [(1, 'pci:0000:03:00.0')]
        assert host.calls == []
        assert "GPU memory: card pci:0000:01:00.0 'Test Card A' is not read: the source is pinned to amdgpu-sysfs." \
            in caplog.messages

    def test_at_most_sixteen_cards(self, host):
        for bus in range(20):
            host.card(f'0000:{bus + 1:02x}:00.0', AMD, files=amd_files())
        reading = opened(host).sample(frozenset())
        assert [gpu.index for gpu in reading.gpus] == list(range(16))


class TestLaterFailures:

    def test_an_amd_card_failing_later_makes_the_sample_none(self, host):
        real = host.card('0000:03:00.0', AMD, files=amd_files())
        host.card('0000:23:00.0', AMD, files=amd_files())
        backend = opened(host)
        del host.files[f'{real}/mem_info_vram_used']
        assert backend.sample(frozenset()) is None

    @pytest.mark.parametrize('later', [None, "1, 00000000:02:00.0, 16303, 200, Test Card B\n", 'garbage'])
    def test_an_nvidia_card_failing_later_makes_the_sample_none(self, host, later):
        host.card('0000:01:00.0', NVIDIA)
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS}
        backend = opened(host)
        host.outputs = {QUERY_GPU_FLAG: later}
        assert backend.sample(frozenset()) is None

    def test_a_card_unreadable_at_open_never_spoils_a_sample(self, host):
        host.card('0000:01:00.0', NVIDIA)
        host.card('0000:05:00.0', AMD, files={})
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS}
        backend = opened(host)
        host.files.update({'/sys/devices/pci0000:00/0000:05:00.0/' + name: value
                           for name, value in amd_files().items()})
        assert [gpu.card_key for gpu in backend.sample(frozenset()).gpus] == ['pci:0000:01:00.0']

    def test_close_holds_nothing(self, host):
        host.card('0000:03:00.0', AMD, files=amd_files())
        backend = opened(host)
        backend.close()
        assert backend.sample(frozenset()) is not None


class TestThroughTheReader:

    def factories(self, host):
        return {'linux-drm': host.backend, 'amdgpu-sysfs': lambda: host.backend(amd_only=True),
                'nvidia-smi': lambda: nvidia_smi.NvidiaSmiBackend(run=host.run, clock=lambda: 7.0)}

    def test_auto_on_linux_chooses_linux_drm(self, host):
        host.card('0000:03:00.0', AMD, files=amd_files())
        reader = reader_module.GpuMemoryReader('auto', 'linux', self.factories(host))
        assert reader.read(lambda: frozenset()).source == 'amdgpu-sysfs' and reader.source == 'linux-drm'

    def test_no_pci_list_gives_nvidia_smi_alone(self, host):
        host.listable = False
        host.outputs = {QUERY_GPU_FLAG: TWO_ROWS, PIDS_FLAG: BUSY}
        reader = reader_module.GpuMemoryReader('auto', 'linux', self.factories(host))
        reading = reader.read(lambda: frozenset({4300}))
        assert reader.source == 'nvidia-smi' and reading.source == 'nvidia-smi'
        assert [(gpu.index, gpu.card_key, gpu.sethlans_used_mb) for gpu in reading.gpus] == [
            (0, 'pci:0000:02:00.0', 50), (1, 'pci:0000:01:00.0', 0)]

    def test_pinned_amdgpu_sysfs(self, host):
        host.card('0000:03:00.0', AMD, files=amd_files())
        reader = reader_module.GpuMemoryReader('amdgpu-sysfs', 'linux', self.factories(host))
        assert reader.read(lambda: frozenset()).source == 'amdgpu-sysfs' and reader.source == 'amdgpu-sysfs'

    def test_the_probe_gets_the_sys_root(self, host):
        host.card('0000:03:00.0', AMD, files=amd_files())
        opened(host)
        assert host.probes == ['/sys']


def test_default_backend_reads_the_real_paths():
    backend = linux_backend.LinuxDrmBackend()
    assert backend.name == 'linux-drm' and backend._sys_root == '/sys'
    assert backend._probe is linux_backend.os_probe_linux.probe
