# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``nvidia-smi`` backend of the GPU memory reader (moved from the Unreal
``vram`` tests; new column order with ``pci.bus_id``; System32 only on
Windows; attribution from ``-q -d PIDS`` by card key off Windows).
"""
import subprocess

import pytest

from sethlans_worker_agent.gpu_memory import nvidia_smi
from tests.unit.worker.gpu_memory._nvidia_fixtures import BUS_A, BUS_B, BUSY, IDLE, gpu_block, listing

ONE_GPU = "0, 00000000:01:00.0, 32607, 1012, NVIDIA GeForce RTX 5090\n"
TWO_GPUS = ("0, 00000000:01:00.0, 24564, 2000, NVIDIA GeForce RTX 4090\n"
            "1, 00000000:02:00.0, 24576, 100, NVIDIA GeForce RTX 3090\n")
QUERY_GPU_FLAG = nvidia_smi.QUERY_GPU[1]
PIDS_FLAG = nvidia_smi.QUERY_PIDS[1]
NA_PIDS = listing(gpu_block(BUS_A, (4100, 'G', 'render-app', 'N/A'), (4200, 'G', 'desktop-shell', 'N/A')))


def fake_run(outputs):
    calls = []

    def run(args):
        calls.append(args)
        return outputs.get(args[1])

    run.calls = calls
    return run


def backend(outputs, clock=lambda: 5.0):
    run = fake_run(outputs)
    return nvidia_smi.NvidiaSmiBackend(run=run, clock=clock), run


class TestParse:

    def test_one_gpu(self):
        assert nvidia_smi.parse_query_gpu(ONE_GPU) == [(0, 'NVIDIA GeForce RTX 5090', 32607, 1012, 'pci:0000:01:00.0')]

    def test_two_gpus(self):
        assert [row[0] for row in nvidia_smi.parse_query_gpu(TWO_GPUS)] == [0, 1]

    def test_na_fields(self):
        text = ("0, 00000000:01:00.0, 8192, 10, [N/A]\n1, 00000000:02:00.0, [N/A], 10, Tesla\n"
                "2, 00000000:03:00.0, 8192, [N/A], Tesla\n[N/A], 00000000:04:00.0, 1, 1, X\n")
        assert nvidia_smi.parse_query_gpu(text) == [(0, '', 8192, 10, 'pci:0000:01:00.0')]

    def test_name_with_commas_and_garbage_lines(self):
        text = "\n0, 00000000:01:00.0, 4096, 5000, Quadro, Special\nnot a row\n"
        assert nvidia_smi.parse_query_gpu(text) == [(0, 'Quadro, Special', 4096, 4096, 'pci:0000:01:00.0')]

    def test_bad_bus_id_gives_no_card_key(self):
        assert nvidia_smi.parse_query_gpu("0, [N/A], 4096, 10, X\n") == [(0, 'X', 4096, 10, None)]

    def test_eight_digit_upper_case_bus_id_normalises(self):
        rows = nvidia_smi.parse_query_gpu("0, 00000000:0A:00.0, 16000, 10, Test Card A\n")
        assert rows == [(0, 'Test Card A', 16000, 10, 'pci:0000:0a:00.0')]


@pytest.fixture()
def posix(monkeypatch):
    """Attribution through ``-q -d PIDS`` is only attempted off Windows."""
    monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: False)


class TestSample:

    def test_reading_without_attribution_when_memory_is_not_a_number(self, posix):
        source, run = backend({QUERY_GPU_FLAG: ONE_GPU, PIDS_FLAG: NA_PIDS})
        reading = source.sample(frozenset({4100}))
        assert reading.source == 'nvidia-smi' and reading.taken_at == 5.0
        assert reading.measures_card is True and reading.measures_process is False
        assert reading.gpus == (nvidia_smi.make_gpu(0, 'NVIDIA GeForce RTX 5090', 32607, 1012, None,
                                                    'pci:0000:01:00.0'),)
        assert run.calls == [nvidia_smi.QUERY_GPU, nvidia_smi.QUERY_PIDS]  # one listing, no uuid query

    def test_windows_never_attributes(self, monkeypatch):
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)
        source, run = backend({QUERY_GPU_FLAG: ONE_GPU, PIDS_FLAG: BUSY})
        [gpu] = source.sample(frozenset({4100})).gpus
        assert gpu.sethlans_used_mb is None and gpu.other_used_mb is None
        assert [call[1] for call in run.calls] == [QUERY_GPU_FLAG]  # the listing is not even asked

    def test_attribution_by_card_key_includes_graphics_processes(self, posix):
        source, _run = backend({QUERY_GPU_FLAG: TWO_GPUS, PIDS_FLAG: BUSY})
        reading = source.sample(frozenset({4100, 4300}))
        first, second = reading.gpus
        assert (first.sethlans_used_mb, first.other_used_mb) == (600, 1400)  # the desktop shell is "other"
        assert (second.sethlans_used_mb, second.other_used_mb) == (50, 50)
        assert reading.measures_process is True

    def test_a_block_the_card_query_does_not_list_distrusts_the_listing(self, posix):
        source, _run = backend({QUERY_GPU_FLAG: ONE_GPU, PIDS_FLAG: BUSY})  # BUSY also lists card B
        [gpu] = source.sample(frozenset({4100})).gpus
        assert gpu.sethlans_used_mb is None

    def test_cards_past_the_cap_still_count_as_known_for_the_listing(self, posix):
        buses = [f"00000000:{i + 1:02X}:00.0" for i in range(20)]
        rows = ''.join(f"{i}, {bus}, 1024, 100, Card\n" for i, bus in enumerate(buses))
        pids = listing(gpu_block(buses[0], (4100, 'G', 'render-app', '60 MiB')), *(gpu_block(bus) for bus in buses[1:]))
        source, _run = backend({QUERY_GPU_FLAG: rows, PIDS_FLAG: pids})
        reading = source.sample(frozenset({4100}))
        assert len(reading.gpus) == 16 and reading.gpus[0].sethlans_used_mb == 60 and reading.measures_process is True

    def test_an_idle_card_is_attributed_zero(self, posix):
        source, _run = backend({QUERY_GPU_FLAG: ONE_GPU, PIDS_FLAG: IDLE})
        [gpu] = source.sample(frozenset({4100})).gpus
        assert (gpu.sethlans_used_mb, gpu.other_used_mb) == (0, 1012)

    def test_mixed_na_nulls_only_that_gpu(self, posix):
        pids = listing(gpu_block(BUS_A, (4100, 'G', 'a', 'N/A')), gpu_block(BUS_B, (4300, 'C', 'b', '40 MiB')))
        source, _run = backend({QUERY_GPU_FLAG: TWO_GPUS, PIDS_FLAG: pids})
        reading = source.sample(frozenset({4300}))
        first, second = reading.gpus
        assert first.sethlans_used_mb is None and first.other_used_mb is None
        assert second.sethlans_used_mb == 40 and reading.measures_process is False

    def test_unlisted_gpu_is_not_attributed(self, posix):
        source, _run = backend({QUERY_GPU_FLAG: TWO_GPUS, PIDS_FLAG: listing(gpu_block(BUS_B))})
        first, second = source.sample(frozenset({4300})).gpus
        assert first.sethlans_used_mb is None and second.sethlans_used_mb == 0

    @pytest.mark.parametrize('pids', [None, BUSY.ljust(nvidia_smi.MAX_STDOUT_CHARS + 1)], ids=['failed', 'over-cap'])
    def test_a_failed_listing_attributes_nothing(self, posix, pids):
        source, _run = backend({QUERY_GPU_FLAG: ONE_GPU, PIDS_FLAG: pids})
        reading = source.sample(frozenset({4100}))
        assert reading.gpus[0].sethlans_used_mb is None and reading.measures_process is False

    def test_a_card_without_a_bus_id_is_never_attributed(self, posix):
        source, run = backend({QUERY_GPU_FLAG: "0, [N/A], 4096, 10, X\n", PIDS_FLAG: BUSY})
        [gpu] = source.sample(frozenset({4100})).gpus
        assert gpu.sethlans_used_mb is None and [call[1] for call in run.calls] == [QUERY_GPU_FLAG]

    @pytest.mark.parametrize('outputs', [{}, {QUERY_GPU_FLAG: ''}, {QUERY_GPU_FLAG: 'garbage'}])
    def test_failure_returns_none(self, outputs):
        source, _run = backend(outputs)
        assert source.sample(frozenset()) is None

    def test_at_most_sixteen_cards(self, posix):
        rows = ''.join(f"{i}, 00000000:{i + 1:02x}:00.0, 1024, 0, Card\n" for i in range(20))
        source, _run = backend({QUERY_GPU_FLAG: rows})
        assert len(source.sample(frozenset()).gpus) == 16


class TestExecutable:

    @pytest.mark.parametrize('exc', [FileNotFoundError(), subprocess.TimeoutExpired('nvidia-smi', 10), OSError()])
    def test_run_nvidia_smi_swallows_errors(self, mocker, posix, exc):
        mocker.patch('subprocess.run', side_effect=exc)
        assert nvidia_smi.run_nvidia_smi(nvidia_smi.QUERY_GPU) is None

    def test_run_nvidia_smi_uses_list_form_and_timeout(self, mocker, posix):
        run = mocker.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, ONE_GPU, ''))
        assert nvidia_smi.run_nvidia_smi(nvidia_smi.QUERY_GPU) == ONE_GPU
        args, kwargs = run.call_args
        assert args[0] == ['nvidia-smi'] + nvidia_smi.QUERY_GPU[1:]
        assert kwargs['timeout'] == 10 and 'shell' not in kwargs

    def test_windows_runs_only_the_system32_binary(self, monkeypatch, mocker, tmp_path):
        system32 = tmp_path / 'System32'
        system32.mkdir()
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)
        monkeypatch.setenv('SystemRoot', str(tmp_path))
        run = mocker.patch('subprocess.run')
        assert nvidia_smi.nvidia_smi_executable() is None  # never the bare name from PATH
        assert nvidia_smi.run_nvidia_smi(nvidia_smi.QUERY_GPU) is None and not run.called
        assert nvidia_smi.NvidiaSmiBackend().open() is False
        (system32 / 'nvidia-smi.exe').write_bytes(b'MZ')
        assert nvidia_smi.nvidia_smi_executable() == str(system32 / 'nvidia-smi.exe')
        assert nvidia_smi.NvidiaSmiBackend().open() is True

    @pytest.mark.parametrize('root', ['Windows', r'.\Windows', ''])
    def test_a_relative_system_root_runs_nothing(self, monkeypatch, mocker, tmp_path, root):
        monkeypatch.chdir(tmp_path)
        (tmp_path / 'Windows' / 'System32').mkdir(parents=True)
        (tmp_path / 'Windows' / 'System32' / 'nvidia-smi.exe').write_bytes(b'MZ')
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)
        monkeypatch.setenv('SystemRoot', root)
        run = mocker.patch('subprocess.run')
        assert nvidia_smi.nvidia_smi_executable() is None
        assert nvidia_smi.run_nvidia_smi(nvidia_smi.QUERY_GPU) is None and not run.called

    def test_output_over_the_cap_is_a_failed_call(self, mocker, posix):
        big = ONE_GPU + 'x' * nvidia_smi.MAX_STDOUT_CHARS
        mocker.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, big, ''))
        assert nvidia_smi.run_nvidia_smi(nvidia_smi.QUERY_GPU) is None
        source, _run = backend({QUERY_GPU_FLAG: big})
        assert source.sample(frozenset()) is None  # never a truncated parse
        exact = ONE_GPU.ljust(nvidia_smi.MAX_STDOUT_CHARS)
        source, _run = backend({QUERY_GPU_FLAG: exact})
        assert len(source.sample(frozenset()).gpus) == 1

    def test_path_lookup_off_windows(self, posix):
        assert nvidia_smi.nvidia_smi_executable() == 'nvidia-smi'
        assert nvidia_smi.NvidiaSmiBackend().open() is True

    def test_windows_runs_the_system32_path_never_the_bare_name(self, monkeypatch, mocker, tmp_path):
        """FR-GN-1: every query runs the full System32 path, even with a ``nvidia-smi`` on ``PATH``."""
        system32 = tmp_path / 'System32'
        system32.mkdir()
        (system32 / 'nvidia-smi.exe').write_bytes(b'MZ')
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)
        monkeypatch.setenv('SystemRoot', str(tmp_path))
        mocker.patch('shutil.which', return_value=str(tmp_path / 'elsewhere' / 'nvidia-smi.exe'))
        run = mocker.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, ONE_GPU, ''))
        reading = nvidia_smi.NvidiaSmiBackend().sample(frozenset({100}))
        assert reading is not None and run.call_count == 1  # Windows: card query only, no attribution
        executables = [call.args[0][0] for call in run.call_args_list]
        assert executables == [str(system32 / 'nvidia-smi.exe')]
        assert run.call_args.args[0][1:] == nvidia_smi.QUERY_GPU[1:]
        assert 'shell' not in run.call_args.kwargs and run.call_args.kwargs['timeout'] == 10

    def test_linux_runs_the_bare_name_for_every_query(self, posix, mocker):
        outputs = {QUERY_GPU_FLAG: ONE_GPU, PIDS_FLAG: listing(gpu_block(BUS_A, (4100, 'G', 'render-app', '600 MiB')))}

        def fake(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, outputs[args[1]], '')

        run = mocker.patch('subprocess.run', side_effect=fake)
        reading = nvidia_smi.NvidiaSmiBackend().sample(frozenset({4100}))
        assert reading.gpus[0].sethlans_used_mb == 600
        assert [call.args[0] for call in run.call_args_list] == [
            ['nvidia-smi'] + nvidia_smi.QUERY_GPU[1:], ['nvidia-smi', '-q', '-d', 'PIDS']]
        assert all('shell' not in call.kwargs and call.kwargs['timeout'] == 10 for call in run.call_args_list)
