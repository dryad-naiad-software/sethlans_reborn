# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``nvidia-smi`` cross-check of a ``pdh`` reading at selection, log only (GPU memory reader FR-GS-6)."""
import logging

from sethlans_worker_agent.gpu_memory import win_crosscheck
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading, make_gpu

LOG = 'sethlans_worker_agent.gpu_memory.win_crosscheck'
READING = GpuMemoryReading(0.0, 'pdh', (make_gpu(0, 'Test Card A', 8000, 600, None, 'pci:0000:0a:00.0'),
                                        make_gpu(1, 'Test Card B', 4000, 100, None, 'pci:0000:0b:00.0')),
                           measures_card=True)
# index, pci.bus_id, total, used, name: card A with an 8-digit domain, an unknown card, no card B
SMI = "0, 00000000:0A:00.0, 8400, 620, Test Card A\n1, 00000000:0C:00.0, 2000, 10, Test Card C\n"


def _run(out):
    calls = []

    def run(args):
        calls.append(args)
        return out
    return run, calls


def test_matched_cards_are_logged_at_info_with_both_totals(caplog):
    caplog.set_level(logging.INFO, logger=LOG)
    run, calls = _run(SMI)
    win_crosscheck.log_against_nvidia_smi(READING, run=run, executable=lambda: 'C:/fake/nvidia-smi.exe')
    assert len(calls) == 1
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ('INFO', "GPU memory cross-check of pci:0000:0a:00.0 'Test Card A': used 600 MiB (pdh) and 620 MiB "
                 "(nvidia-smi), difference -20 MiB; total 8000 MiB (DXGI) and 8400 MiB (nvidia-smi).")]


def test_a_used_difference_above_32_mib_is_a_warning():
    lines = win_crosscheck.compare(READING, [(0, 'Test Card A', 8400, 633, 'pci:0000:0a:00.0')])
    assert [level for level, _text in lines] == [logging.WARNING]
    lines = win_crosscheck.compare(READING, [(0, 'Test Card A', 8400, 632, 'pci:0000:0a:00.0')])
    assert [level for level, _text in lines] == [logging.INFO]


def test_without_the_system32_binary_nothing_runs():
    run, calls = _run(SMI)
    win_crosscheck.log_against_nvidia_smi(READING, run=run, executable=lambda: None)
    assert calls == []


def test_no_answer_is_one_info_line(caplog):
    caplog.set_level(logging.INFO, logger=LOG)
    run, _calls = _run(None)
    win_crosscheck.log_against_nvidia_smi(READING, run=run, executable=lambda: 'x')
    assert caplog.messages == ["GPU memory cross-check: nvidia-smi gave no answer."]


def test_a_failure_never_raises():
    def run(args):
        raise RuntimeError('broken')

    win_crosscheck.log_against_nvidia_smi(READING, run=run, executable=lambda: 'x')
    win_crosscheck.log_against_nvidia_smi(None, run=run, executable=lambda: 'x')
