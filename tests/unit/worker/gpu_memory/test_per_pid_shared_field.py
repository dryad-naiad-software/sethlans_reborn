# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``GpuMemoryReading.per_pid_shared`` (per machine memory test FR-MT-S-1) outside the Windows ``pdh`` backend:
the field's default, hash and ``dataclasses.replace``; ``None`` from the Linux, ``nvidia-smi`` and override
readings; kept through the reader and the Unreal reading cuts. Fakes only; runs on every OS.
"""
import dataclasses

import pytest

from sethlans_worker_agent.gpu_memory import nvidia_smi
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading, make_gpu
from sethlans_worker_agent.unreal_executor import vram
from tests.unit.worker.gpu_memory._linux_fakes import AMD, NVIDIA, PIDS_FLAG, QUERY_GPU_FLAG, amd_files, make_host
from tests.unit.worker.gpu_memory._nvidia_fixtures import BUS_A, BUS_B, BUSY
from tests.unit.worker.gpu_memory._reader_fakes import PIDS, FakeBackend, make_reader, only

KEY_A = 'pci:0000:0a:00.0'
KEY_B = 'pci:0000:0b:00.0'
SHARED = {KEY_A: {4242: 300}, KEY_B: {}}
DEDICATED = {KEY_A: {4242: 1200}, KEY_B: {4343: 40}}


def two_card_reading(**fields):
    gpus = (make_gpu(0, 'Test Card A', 8192, 1500, 1200, KEY_A), make_gpu(1, 'Test Card B', 4096, 100, 40, KEY_B))
    return GpuMemoryReading(5.0, 'pdh', gpus, measures_card=True, measures_process=True, **fields)


class TestField:

    def test_defaults_to_none_on_every_constructor(self):
        assert GpuMemoryReading(1.0, 'fake-a', ()).per_pid_shared is None
        assert GpuMemoryReading(1.0, 'fake-a', (), True, True, DEDICATED).per_pid_shared is None

    def test_the_sixth_positional_field(self):
        reading = GpuMemoryReading(1.0, 'fake-a', (), True, True, DEDICATED, SHARED)
        assert (reading.per_pid, reading.per_pid_shared) == (DEDICATED, SHARED)

    def test_left_out_of_the_hash_but_part_of_equality(self):
        plain, shared = two_card_reading(), two_card_reading(per_pid_shared=SHARED)
        assert hash(plain) == hash(shared)
        assert plain != shared and shared == two_card_reading(per_pid_shared=dict(SHARED))

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            two_card_reading().per_pid_shared = SHARED

    def test_replace_keeps_the_shared_map(self):
        original = two_card_reading(per_pid=DEDICATED, per_pid_shared=SHARED)
        moved = dataclasses.replace(original, taken_at=9.0, gpus=original.gpus[:1])
        assert moved.per_pid_shared is SHARED and moved.per_pid is DEDICATED and moved.taken_at == 9.0

    def test_replace_can_set_and_clear_it(self):
        assert dataclasses.replace(two_card_reading(), per_pid_shared=SHARED).per_pid_shared == SHARED
        assert dataclasses.replace(two_card_reading(per_pid_shared=SHARED), per_pid_shared=None).per_pid_shared is None

    def test_not_in_the_heartbeat_payload(self):
        with_shared = vram.to_payload(two_card_reading(per_pid_shared=SHARED), now=5.0)
        assert with_shared == vram.to_payload(two_card_reading(), now=5.0)
        assert sorted(with_shared) == ['age_s', 'capabilities', 'gpus', 'schema', 'source']


@pytest.fixture
def host(mocker, monkeypatch):
    return make_host(mocker, monkeypatch)


class TestOtherBackends:

    def test_linux_amd_reading_has_no_shared_map(self, host):
        host.card('0000:03:00.0', AMD, 'Test Card A', amd_files(24560, 1000))
        backend = host.backend()
        assert backend.open() is True
        reading = backend.sample(frozenset({4242}))
        assert reading.source == 'amdgpu-sysfs' and reading.per_pid_shared is None

    def test_linux_nvidia_reading_has_no_shared_map(self, host):
        host.card('0000:02:00.0', NVIDIA)
        host.card('0000:01:00.0', NVIDIA)
        host.outputs = {QUERY_GPU_FLAG: ("0, 00000000:02:00.0, 16303, 200, Test Card B\n"
                                         "1, 00000000:01:00.0, 16303, 1500, Test Card A\n"), PIDS_FLAG: BUSY}
        backend = host.backend()
        assert backend.open() is True
        reading = backend.sample(frozenset({4100, 4300}))
        assert reading.source == 'nvidia-smi' and reading.measures_process is True
        assert reading.per_pid_shared is None

    def test_nvidia_smi_reading_with_a_per_pid_map_has_no_shared_map(self, monkeypatch):
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: False)
        cards = f"0, {BUS_A}, 24564, 2000, Test Card A\n1, {BUS_B}, 24576, 100, Test Card B\n"

        def run(args):
            return cards if args[1] == nvidia_smi.QUERY_GPU[1] else BUSY

        reading = nvidia_smi.NvidiaSmiBackend(run=run, clock=lambda: 1.0).sample(frozenset({4100}))
        assert reading.per_pid is not None and reading.per_pid_shared is None

    def test_nvidia_smi_on_windows_has_no_shared_map(self, monkeypatch):
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)
        cards = f"0, {BUS_A}, 24564, 2000, Test Card A\n"
        reading = nvidia_smi.NvidiaSmiBackend(run=lambda args: cards, clock=lambda: 1.0).sample(frozenset({1}))
        assert reading.per_pid_shared is None

    def test_the_vram_override_reading_has_no_shared_map(self):
        reading = vram.take_reading(16384, clock=lambda: 3.0)
        assert reading.source == vram.SOURCE_OVERRIDE and reading.per_pid_shared is None


class TestKeptDownstream:

    def test_the_reader_returns_the_shared_map_unchanged(self, monkeypatch):
        only(monkeypatch, 'pdh')
        sample = two_card_reading(per_pid=DEDICATED, per_pid_shared=SHARED)
        reader, _created = make_reader(FakeBackend('pdh', [], samples=[sample]))
        assert reader.read(lambda: PIDS).per_pid_shared == SHARED

    def test_take_reading_cuts_cards_and_keeps_the_shared_map(self):
        sample = two_card_reading(per_pid=DEDICATED, per_pid_shared=SHARED)
        reading = vram.take_reading(0, clock=lambda: 8.0, read=lambda pids: sample,
                                    cards=lambda: frozenset({(0, 0x0a, 0, 0)}))
        assert [gpu.card_key for gpu in reading.gpus] == [KEY_A] and reading.taken_at == 8.0
        assert reading.per_pid_shared == SHARED

    @pytest.mark.parametrize('cut', [lambda r: vram.only_card(r, KEY_B),
                                     lambda r: vram.cut_to_cards(r, frozenset({(0, 0x0b, 0, 0)})),
                                     lambda r: vram.with_screens(r, {(0, 0x0b, 0, 0): True})],
                             ids=['only_card', 'cut_to_cards', 'with_screens'])
    def test_the_unreal_cuts_keep_the_shared_map(self, cut):
        assert cut(two_card_reading(per_pid_shared=SHARED)).per_pid_shared == SHARED
