# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Windows RAM readers (render per card FR-PC-RAM-1): the ctypes layer with a fake ``kernel32``
(every OS), the structure layouts against the Windows SDK's ``psapi.h`` and one real call of each
reader on this process (Windows only).
"""
import ctypes
import os
import sys

import psutil
import pytest

from sethlans_worker_agent.unreal_executor import process_memory
from sethlans_worker_agent.unreal_executor.process_memory import (
    PerformanceInformation, ProcessMemoryCountersEx2, ProcessMemoryError, Win32MemoryApi)

windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='Windows memory API')
MIB = 1024 * 1024


class FakeKernel32:
    """The four calls the readers make; ``fail`` names the one that returns FALSE (or 0)."""

    def __init__(self, fail=None, filled_cb=None, counters=None, perf=None):
        self.fail, self.filled_cb = fail, filled_cb
        self.counters = counters or {'PrivateWorkingSetSize': 700 * MIB, 'PrivateUsage': 900 * MIB}
        self.perf = perf or {'CommitTotal': 1000, 'CommitLimit': 3000, 'PageSize': 4096}
        self.calls, self.closed = [], []

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(('open', access, inherit, pid))
        return 0 if self.fail == 'open' else 555

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1

    def K32GetProcessMemoryInfo(self, handle, counters, size):
        self.calls.append(('pmi', handle, counters.cb, size))
        if self.fail == 'pmi':
            return 0
        for name, value in self.counters.items():
            setattr(counters, name, value)
        counters.cb = size if self.filled_cb is None else self.filled_cb
        return 1

    def K32GetPerformanceInfo(self, info, size):
        self.calls.append(('perf', info.cb, size))
        if self.fail == 'perf':
            return 0
        for name, value in self.perf.items():
            setattr(info, name, value)
        return 1


def api(**kwargs):
    k32 = FakeKernel32(**kwargs)
    return Win32MemoryApi(kernel32=k32, last_error=lambda: 5), k32


class TestReadersOnFakes:

    def test_private_ws_and_commit_opens_limited_and_passes_the_ex2_size(self):
        fake_api, k32 = api()
        assert process_memory.private_ws_and_commit(4242, fake_api) == (700 * MIB, 900 * MIB)
        size = ctypes.sizeof(ProcessMemoryCountersEx2)
        assert k32.calls == [('open', 0x1000, False, 4242), ('pmi', 555, size, size)]
        assert k32.closed == [555]

    def test_a_process_that_cannot_be_opened_raises_with_the_windows_error(self):
        fake_api, k32 = api(fail='open')
        with pytest.raises(ProcessMemoryError) as caught:
            process_memory.private_ws_and_commit(4242, fake_api)
        assert caught.value.errno == 5 and 'OpenProcess(4242)' in str(caught.value)
        assert k32.closed == []

    def test_a_failed_call_raises_and_closes_the_handle(self):
        fake_api, k32 = api(fail='pmi')
        with pytest.raises(ProcessMemoryError, match='GetProcessMemoryInfo'):
            process_memory.private_ws_and_commit(4242, fake_api)
        assert k32.closed == [555]

    @pytest.mark.parametrize('filled', [80, 88])
    def test_a_shorter_structure_filled_raises(self, filled):
        """A Windows build without EX2 fills a shorter structure and says so in ``cb`` (U-6): never a 0 figure."""
        fake_api, k32 = api(filled_cb=filled)
        with pytest.raises(ProcessMemoryError, match='PROCESS_MEMORY_COUNTERS_EX2'):
            process_memory.private_ws_and_commit(4242, fake_api)
        assert k32.closed == [555]

    def test_commit_headroom_is_limit_minus_total_in_bytes(self):
        fake_api, k32 = api()
        assert process_memory.commit_headroom(fake_api) == 2000 * 4096
        size = ctypes.sizeof(PerformanceInformation)
        assert k32.calls == [('perf', size, size)]

    def test_commit_headroom_can_be_negative(self):
        fake_api, _k32 = api(perf={'CommitTotal': 3100, 'CommitLimit': 3000, 'PageSize': 4096})
        assert process_memory.commit_headroom(fake_api) == -100 * 4096

    def test_a_failed_performance_call_raises(self):
        fake_api, _k32 = api(fail='perf')
        with pytest.raises(ProcessMemoryError, match='GetPerformanceInfo') as caught:
            process_memory.commit_headroom(fake_api)
        assert caught.value.errno == 5

    @pytest.mark.parametrize('perf', [{'CommitTotal': 1, 'CommitLimit': 3000, 'PageSize': 0},
                                      {'CommitTotal': 0, 'CommitLimit': 0, 'PageSize': 4096}])
    def test_impossible_performance_figures_raise(self, perf):
        fake_api, _k32 = api(perf=perf)
        with pytest.raises(ProcessMemoryError):
            process_memory.commit_headroom(fake_api)

    @pytest.mark.skipif(sys.platform == 'win32', reason='checks the refusal off Windows')
    def test_off_windows_the_real_layer_raises(self):
        with pytest.raises(ProcessMemoryError, match='only read on Windows'):
            process_memory.private_ws_and_commit(os.getpid())


@windows_only
@pytest.mark.skipif(sys.maxsize <= 2 ** 32, reason='64-bit layout')
class TestLayout:
    """Sizes and offsets of the Windows SDK 10.0.26100.0 ``psapi.h`` structures on x64."""

    def test_process_memory_counters_ex2(self):
        assert ctypes.sizeof(ProcessMemoryCountersEx2) == 96
        assert ProcessMemoryCountersEx2.PageFaultCount.offset == 4
        assert ProcessMemoryCountersEx2.PeakWorkingSetSize.offset == 8
        assert ProcessMemoryCountersEx2.PrivateUsage.offset == 72
        assert ProcessMemoryCountersEx2.PrivateWorkingSetSize.offset == 80
        assert ProcessMemoryCountersEx2.SharedCommitUsage.offset == 88

    def test_performance_information(self):
        assert ctypes.sizeof(PerformanceInformation) == 104
        assert PerformanceInformation.CommitTotal.offset == 8
        assert PerformanceInformation.CommitLimit.offset == 16
        assert PerformanceInformation.PageSize.offset == 80
        assert PerformanceInformation.HandleCount.offset == 88
        assert PerformanceInformation.ThreadCount.offset == 96


@windows_only
class TestRealCalls:

    def test_this_process(self):
        private_ws, private_commit = process_memory.private_ws_and_commit(os.getpid())
        assert 0 < private_ws and 0 < private_commit
        assert abs(private_commit - psutil.Process().memory_info().private) < 16 * MIB  # the same counter

    def test_commit_headroom(self):
        headroom = process_memory.commit_headroom()
        assert 0 < headroom < psutil.virtual_memory().total + psutil.swap_memory().total + 64 * 1024 * MIB

    def test_a_pid_that_does_not_exist_raises(self):
        with pytest.raises(ProcessMemoryError, match='OpenProcess'):
            process_memory.private_ws_and_commit(0)
