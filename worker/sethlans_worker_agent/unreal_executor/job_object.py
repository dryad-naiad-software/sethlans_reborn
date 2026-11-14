# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One Windows Job Object per UE launch (Unreal foundation FR-UE-11; Conc H1).

* ``JobObject.create()`` makes an unnamed job with
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. The handle is **not inheritable**
  (``CreateJobObjectW(NULL, NULL)``) and only this process holds it: if the
  worker dies for any reason the kernel closes it and kills every member.
* ``assign(process_handle)`` puts a (suspended) process into the job.
* ``terminate()`` (``TerminateJobObject``) kills every member at once.
* ``active_processes()`` (``JobObjectBasicAccountingInformation``) and
  ``pids()`` (``JobObjectBasicProcessIdList``) enumerate members, which
  includes processes that are not UE's descendants (``crashpad_handler``,
  ``CrashReportClientEditor``, ``UnrealTraceServer``; S2).

**Fail closed:** every failure raises ``JobObjectError``; the caller
kills the process and refuses to run unconfined. The ctypes pattern
follows ``shared/caddy_supervisor/process.py`` (copied, not imported:
those helpers are private and fail open). ``ctypes`` is loaded on first
use, so the module imports on every platform; the Win32 layer
(``Win32JobApi``) is a class so unit tests can pass a fake.
"""
import threading
from typing import List, Optional

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_BASIC_ACCOUNTING = 1
_JOB_BASIC_PROCESS_ID_LIST = 3
_JOB_EXTENDED_LIMIT = 9
_ERROR_MORE_DATA = 234
MAX_LISTED_PIDS = 4096


class JobObjectError(OSError):
    """A Job Object call failed; the launch must not run unconfined."""


class Win32JobApi:
    """Thin ctypes wrapper; every method raises ``JobObjectError`` on failure."""

    _lock = threading.Lock()  # leaf lock (structure setup)
    _ns = None

    def _load(self):
        with Win32JobApi._lock:
            if Win32JobApi._ns is not None:
                return Win32JobApi._ns
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            dword, handle, boolean, vp = wintypes.DWORD, wintypes.HANDLE, wintypes.BOOL, ctypes.c_void_p
            for func, argtypes, restype in (
                    (k32.CreateJobObjectW, [vp, wintypes.LPCWSTR], handle),
                    (k32.SetInformationJobObject, [handle, ctypes.c_int, vp, dword], boolean),
                    (k32.AssignProcessToJobObject, [handle, handle], boolean),
                    (k32.TerminateJobObject, [handle, wintypes.UINT], boolean),
                    (k32.QueryInformationJobObject, [handle, ctypes.c_int, vp, dword, ctypes.POINTER(dword)],
                     boolean),
                    (k32.CloseHandle, [handle], boolean)):
                func.argtypes, func.restype = argtypes, restype

            class BasicLimit(ctypes.Structure):
                _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                            ('LimitFlags', dword), ('MinimumWorkingSetSize', ctypes.c_size_t),
                            ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', dword),
                            ('Affinity', ctypes.c_size_t), ('PriorityClass', dword), ('SchedulingClass', dword)]

            class IoCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in (
                    'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                    'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

            class ExtendedLimit(ctypes.Structure):
                _fields_ = [('BasicLimitInformation', BasicLimit), ('IoInfo', IoCounters),
                            ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                            ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

            class BasicAccounting(ctypes.Structure):
                _fields_ = [('TotalUserTime', ctypes.c_int64), ('TotalKernelTime', ctypes.c_int64),
                            ('ThisPeriodTotalUserTime', ctypes.c_int64),
                            ('ThisPeriodTotalKernelTime', ctypes.c_int64),
                            ('TotalPageFaultCount', dword), ('TotalProcesses', dword),
                            ('ActiveProcesses', dword), ('TotalTerminatedProcesses', dword)]

            Win32JobApi._ns = {'ctypes': ctypes, 'wintypes': wintypes, 'k32': k32, 'ExtendedLimit': ExtendedLimit,
                               'BasicAccounting': BasicAccounting}
            return Win32JobApi._ns

    def _fail(self, what: str) -> JobObjectError:
        code = self._load()['ctypes'].get_last_error()
        return JobObjectError(code, f"{what} failed (Windows error {code})")

    def create(self) -> int:
        ns = self._load()
        handle = ns['k32'].CreateJobObjectW(None, None)
        if not handle:
            raise self._fail('CreateJobObjectW')
        return int(handle)

    def set_kill_on_close(self, handle: int) -> None:
        ns = self._load()
        info = ns['ExtendedLimit']()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ctypes = ns['ctypes']
        if not ns['k32'].SetInformationJobObject(handle, _JOB_EXTENDED_LIMIT, ctypes.byref(info),
                                                 ctypes.sizeof(info)):
            raise self._fail('SetInformationJobObject')

    def assign(self, handle: int, process_handle: int) -> None:
        if not self._load()['k32'].AssignProcessToJobObject(handle, process_handle):
            raise self._fail('AssignProcessToJobObject')

    def terminate(self, handle: int, exit_code: int) -> None:
        if not self._load()['k32'].TerminateJobObject(handle, exit_code):
            raise self._fail('TerminateJobObject')

    def active_processes(self, handle: int) -> int:
        ns = self._load()
        ctypes, info = ns['ctypes'], ns['BasicAccounting']()
        if not ns['k32'].QueryInformationJobObject(handle, _JOB_BASIC_ACCOUNTING, ctypes.byref(info),
                                                   ctypes.sizeof(info), None):
            raise self._fail('QueryInformationJobObject(BasicAccounting)')
        return int(info.ActiveProcesses)

    def pids(self, handle: int) -> List[int]:
        ns = self._load()
        ctypes = ns['ctypes']
        count = 64
        while True:
            # JOBOBJECT_BASIC_PROCESS_ID_LIST: two DWORDs, then ULONG_PTR ids (pointer-aligned).
            header = 8
            buf = ctypes.create_string_buffer(header + count * ctypes.sizeof(ctypes.c_size_t))
            if ns['k32'].QueryInformationJobObject(handle, _JOB_BASIC_PROCESS_ID_LIST, buf, len(buf), None):
                listed = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))[1]
                ids = ctypes.cast(ctypes.byref(buf, header), ctypes.POINTER(ctypes.c_size_t))
                return [int(ids[i]) for i in range(min(listed, count))]
            if ctypes.get_last_error() != _ERROR_MORE_DATA or count >= MAX_LISTED_PIDS:
                raise self._fail('QueryInformationJobObject(BasicProcessIdList)')
            count *= 4

    def close(self, handle: int) -> None:
        self._load()['k32'].CloseHandle(handle)


class JobObject:
    """One kill-on-close Job Object. Not thread-safe; owned by one runner."""

    def __init__(self, api, handle: int):
        self._api = api
        self._handle: Optional[int] = handle

    @classmethod
    def create(cls, api=None) -> 'JobObject':
        api = api or Win32JobApi()
        handle = api.create()
        try:
            api.set_kill_on_close(handle)
        except BaseException:
            api.close(handle)
            raise
        return cls(api, handle)

    @property
    def closed(self) -> bool:
        return self._handle is None

    def _require(self) -> int:
        if self._handle is None:
            raise JobObjectError("the Job Object is closed")
        return self._handle

    def assign(self, process_handle: int) -> None:
        self._api.assign(self._require(), int(process_handle))

    def terminate(self, exit_code: int = 1) -> None:
        self._api.terminate(self._require(), exit_code)

    def active_processes(self) -> int:
        return self._api.active_processes(self._require())

    def pids(self) -> List[int]:
        return self._api.pids(self._require())

    def close(self) -> None:
        """Close the handle (KILL_ON_JOB_CLOSE kills anything left). Idempotent."""
        handle, self._handle = self._handle, None
        if handle is not None:
            self._api.close(handle)
