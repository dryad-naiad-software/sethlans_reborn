# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``nvidia-smi`` backend of the GPU memory reader (moved from the Unreal
``vram`` tests; new column order with ``pci.bus_id``; System32 only on
Windows).
"""
import subprocess

import pytest

from sethlans_worker_agent.gpu_memory import nvidia_smi

ONE_GPU = "0, 00000000:01:00.0, 32607, 1012, NVIDIA GeForce RTX 5090\n"
TWO_GPUS = ("0, 00000000:01:00.0, 24564, 2000, NVIDIA GeForce RTX 4090\n"
            "1, 00000000:02:00.0, 24576, 100, NVIDIA GeForce RTX 3090\n")
UUIDS = "0, GPU-aaa\n1, GPU-bbb\n"
QUERY_GPU_FLAG = nvidia_smi.QUERY_GPU[1]
APPS_FLAG = nvidia_smi.QUERY_APPS[1]
UUID_FLAG = nvidia_smi.QUERY_UUIDS[1]
WDDM_APPS = ("GPU-aaa, 10512, [N/A]\n"
             "GPU-aaa, 31392, [N/A]\n")


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

    def test_compute_apps(self):
        assert nvidia_smi.parse_compute_apps(WDDM_APPS) == [('GPU-aaa', 10512, None), ('GPU-aaa', 31392, None)]


@pytest.fixture()
def posix(monkeypatch):
    """Attribution through compute-apps is only attempted off Windows."""
    monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: False)


class TestSample:

    def test_reading_without_attribution_under_wddm(self, posix):
        source, run = backend({QUERY_GPU_FLAG: ONE_GPU, APPS_FLAG: WDDM_APPS})
        reading = source.sample(frozenset({31392}))
        assert reading.source == 'nvidia-smi' and reading.taken_at == 5.0
        assert reading.measures_card is True and reading.measures_process is False
        assert reading.gpus == (nvidia_smi.make_gpu(0, 'NVIDIA GeForce RTX 5090', 32607, 1012, None,
                                                    'pci:0000:01:00.0'),)
        assert [call[1] for call in run.calls] == [QUERY_GPU_FLAG, APPS_FLAG]  # no uuid query

    def test_windows_never_attributes(self, monkeypatch):
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)
        source, run = backend({QUERY_GPU_FLAG: ONE_GPU, APPS_FLAG: "GPU-aaa, 100, 600\n", UUID_FLAG: "0, GPU-aaa\n"})
        [gpu] = source.sample(frozenset({100})).gpus
        assert gpu.sethlans_used_mb is None and gpu.other_used_mb is None
        assert [call[1] for call in run.calls] == [QUERY_GPU_FLAG]  # compute-apps not even asked

    def test_attribution_when_numbers_are_available(self, posix):
        apps = "GPU-aaa, 100, 600\nGPU-aaa, 200, 300\nGPU-bbb, 300, 50\n"
        source, _run = backend({QUERY_GPU_FLAG: TWO_GPUS, APPS_FLAG: apps, UUID_FLAG: UUIDS})
        reading = source.sample(frozenset({100, 300}))
        first, second = reading.gpus
        assert (first.sethlans_used_mb, first.other_used_mb) == (600, 1400)
        assert (second.sethlans_used_mb, second.other_used_mb) == (50, 50)
        assert reading.measures_process is True

    def test_mixed_na_nulls_only_that_gpu(self, posix):
        source, _run = backend({QUERY_GPU_FLAG: TWO_GPUS, APPS_FLAG: "GPU-aaa, 100, [N/A]\nGPU-bbb, 300, 40\n",
                                UUID_FLAG: UUIDS})
        reading = source.sample(frozenset({300}))
        first, second = reading.gpus
        assert first.sethlans_used_mb is None and first.other_used_mb is None
        assert second.sethlans_used_mb == 40 and reading.measures_process is False

    def test_unlisted_gpu_is_not_attributed(self, posix):
        source, _run = backend({QUERY_GPU_FLAG: TWO_GPUS, APPS_FLAG: "GPU-bbb, 300, 40\n", UUID_FLAG: UUIDS})
        first, _second = source.sample(frozenset({300})).gpus
        assert first.sethlans_used_mb is None

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
        outputs = {QUERY_GPU_FLAG: ONE_GPU, APPS_FLAG: "GPU-aaa, 100, 600\n", UUID_FLAG: "0, GPU-aaa\n"}

        def fake(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, outputs[args[1]], '')

        run = mocker.patch('subprocess.run', side_effect=fake)
        reading = nvidia_smi.NvidiaSmiBackend().sample(frozenset({100}))
        assert reading.gpus[0].sethlans_used_mb == 600
        assert [call.args[0][0] for call in run.call_args_list] == ['nvidia-smi'] * 3
        assert [call.args[0][1] for call in run.call_args_list] == [QUERY_GPU_FLAG, APPS_FLAG, UUID_FLAG]
