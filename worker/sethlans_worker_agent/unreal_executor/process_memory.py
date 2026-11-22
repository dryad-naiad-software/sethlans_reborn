# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-process RAM and the machine's commit headroom on Windows (render per card FR-PC-RAM-1).

* ``private_ws_and_commit(pid) -> (private_working_set, private_commit)``, bytes:
  ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` then ``GetProcessMemoryInfo`` with
  ``PROCESS_MEMORY_COUNTERS_EX2`` (``cb`` = its size), returning ``(PrivateWorkingSetSize,
  PrivateUsage)``.
* ``commit_headroom() -> int``, bytes: ``(CommitLimit - CommitTotal) * PageSize`` from
  ``GetPerformanceInfo``. It can be negative when the commit charge is over the limit.

The functions are the ``K32`` exports of ``kernel32`` (``K32GetProcessMemoryInfo``,
``K32GetPerformanceInfo``), which is what ``psapi.h`` maps the names to with ``PSAPI_VERSION`` 2
(Windows SDK 10.0.26100.0, ``psapi.h:61-74``). The structures follow that SDK's ``psapi.h``
(``PROCESS_MEMORY_COUNTERS_EX2`` at ``:501-515``, ``PERFORMANCE_INFORMATION`` at ``:534-549``),
with fixed-width ``ctypes`` types (``DWORD`` = ``c_uint32``, ``SIZE_T`` = ``c_size_t``, ``ULONG64``
= ``c_uint64``). On 64-bit Windows they are 96 and 104 bytes.

**Every failure raises** ``ProcessMemoryError`` (no fallback, NF-2): the process cannot be opened
or is gone, a call fails, or a Windows build fills a shorter structure than ``EX2`` (its returned
``cb`` is not the size asked for; U-6). Off Windows the Win32 layer raises it too, before it
touches any Windows-only ``ctypes`` name (``WinDLL``, ``wintypes``, ``get_last_error``). The Win32 layer is a
class (``Win32MemoryApi``) so tests can pass a fake ``kernel32``.
"""
import ctypes
import sys
import threading
from typing import Callable, Optional, Tuple

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_DWORD = ctypes.c_uint32
_SIZE_T = ctypes.c_size_t


class ProcessMemoryError(OSError):
    """A memory figure could not be read; the caller records it as unknown."""


class ProcessMemoryCountersEx2(ctypes.Structure):
    """``PROCESS_MEMORY_COUNTERS_EX2`` (``psapi.h``)."""
    _fields_ = [('cb', _DWORD), ('PageFaultCount', _DWORD),
                ('PeakWorkingSetSize', _SIZE_T), ('WorkingSetSize', _SIZE_T),
                ('QuotaPeakPagedPoolUsage', _SIZE_T), ('QuotaPagedPoolUsage', _SIZE_T),
                ('QuotaPeakNonPagedPoolUsage', _SIZE_T), ('QuotaNonPagedPoolUsage', _SIZE_T),
                ('PagefileUsage', _SIZE_T), ('PeakPagefileUsage', _SIZE_T),
                ('PrivateUsage', _SIZE_T), ('PrivateWorkingSetSize', _SIZE_T),
                ('SharedCommitUsage', ctypes.c_uint64)]


class PerformanceInformation(ctypes.Structure):
    """``PERFORMANCE_INFORMATION`` (``psapi.h``); the sizes are in pages except ``PageSize``."""
    _fields_ = [('cb', _DWORD),
                ('CommitTotal', _SIZE_T), ('CommitLimit', _SIZE_T), ('CommitPeak', _SIZE_T),
                ('PhysicalTotal', _SIZE_T), ('PhysicalAvailable', _SIZE_T), ('SystemCache', _SIZE_T),
                ('KernelTotal', _SIZE_T), ('KernelPaged', _SIZE_T), ('KernelNonpaged', _SIZE_T),
                ('PageSize', _SIZE_T),
                ('HandleCount', _DWORD), ('ProcessCount', _DWORD), ('ThreadCount', _DWORD)]


def _require_windows() -> None:
    """Raise ``ProcessMemoryError`` off Windows, before any Windows-only ``ctypes`` name is touched."""
    if sys.platform != 'win32':
        raise ProcessMemoryError("process memory figures are only read on Windows")


def _windows_last_error() -> int:
    _require_windows()
    return ctypes.get_last_error()


def _load_kernel32():
    _require_windows()
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle, boolean = wintypes.HANDLE, wintypes.BOOL
    for func, argtypes, restype in (
            (k32.OpenProcess, [_DWORD, boolean, _DWORD], handle),
            (k32.CloseHandle, [handle], boolean),
            (k32.K32GetProcessMemoryInfo, [handle, ctypes.POINTER(ProcessMemoryCountersEx2), _DWORD], boolean),
            (k32.K32GetPerformanceInfo, [ctypes.POINTER(PerformanceInformation), _DWORD], boolean)):
        func.argtypes, func.restype = argtypes, restype
    return k32


class Win32MemoryApi:
    """The ctypes layer; every method raises ``ProcessMemoryError`` on failure.

    ``kernel32`` and ``last_error`` are for tests; by default the real DLL is loaded on first use.
    Structures are passed as instances (ctypes passes them by reference for a ``POINTER`` argtype).
    """

    _lock = threading.Lock()  # leaf lock (DLL setup)
    _shared = None

    def __init__(self, kernel32=None, last_error: Optional[Callable[[], int]] = None):
        self._k32 = kernel32
        self._last_error = last_error or _windows_last_error

    def _kernel32(self):
        if self._k32 is not None:
            return self._k32
        with Win32MemoryApi._lock:
            if Win32MemoryApi._shared is None:
                Win32MemoryApi._shared = _load_kernel32()
            self._k32 = Win32MemoryApi._shared
        return self._k32

    def _fail(self, what: str) -> ProcessMemoryError:
        code = self._last_error()
        return ProcessMemoryError(code, f"{what} failed (Windows error {code})")

    def process_counters(self, pid: int) -> ProcessMemoryCountersEx2:
        """The process's ``PROCESS_MEMORY_COUNTERS_EX2``."""
        k32 = self._kernel32()
        process = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not process:
            raise self._fail(f'OpenProcess({int(pid)})')
        try:
            counters = ProcessMemoryCountersEx2()
            size = ctypes.sizeof(counters)
            counters.cb = size
            if not k32.K32GetProcessMemoryInfo(process, counters, size):
                raise self._fail(f'GetProcessMemoryInfo({int(pid)})')
        finally:
            k32.CloseHandle(process)
        if counters.cb != size:
            raise ProcessMemoryError(f"GetProcessMemoryInfo({int(pid)}) filled {counters.cb} bytes, not the "
                                     f"{size} of PROCESS_MEMORY_COUNTERS_EX2 (this Windows build lacks it)")
        return counters

    def performance(self) -> PerformanceInformation:
        """The system's ``PERFORMANCE_INFORMATION``."""
        info = PerformanceInformation()
        size = ctypes.sizeof(info)
        info.cb = size
        if not self._kernel32().K32GetPerformanceInfo(info, size):
            raise self._fail('GetPerformanceInfo')
        return info


def private_ws_and_commit(pid: int, api: Optional[Win32MemoryApi] = None) -> Tuple[int, int]:
    """``(PrivateWorkingSetSize, PrivateUsage)`` of ``pid`` in bytes; raises ``ProcessMemoryError``."""
    counters = (api or Win32MemoryApi()).process_counters(pid)
    return int(counters.PrivateWorkingSetSize), int(counters.PrivateUsage)


def commit_headroom(api: Optional[Win32MemoryApi] = None) -> int:
    """``(CommitLimit - CommitTotal) * PageSize`` in bytes; raises ``ProcessMemoryError``."""
    info = (api or Win32MemoryApi()).performance()
    if info.PageSize <= 0 or info.CommitLimit <= 0:
        raise ProcessMemoryError(f"GetPerformanceInfo gave page size {info.PageSize} and commit limit "
                                 f"{info.CommitLimit}")
    return (int(info.CommitLimit) - int(info.CommitTotal)) * int(info.PageSize)
