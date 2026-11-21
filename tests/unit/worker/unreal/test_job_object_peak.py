# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``Win32JobApi.peak_memory`` (render per card FR-PC-RAM-1): the ``JOBOBJECT_EXTENDED_LIMIT_INFORMATION``
layout against the Windows SDK's ``winnt.h``, and real Job Objects (Windows only): an empty one, and
one whose member commits 256 MiB and exits before the peak is read (the order U-3 relies on).
"""
import ctypes
import subprocess
import sys

import pytest

from sethlans_worker_agent.unreal_executor import job_object
from sethlans_worker_agent.unreal_executor.job_object import JobObject, JobObjectError, Win32JobApi

MIB = 1024 * 1024
windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job Objects')
_PROCESS_SET_QUOTA, _PROCESS_TERMINATE = 0x0100, 0x0001
# Waits for a line on stdin (so it is in the job before it allocates), commits 256 MiB, touches it, exits.
CHILD = "import sys; sys.stdin.readline(); block = bytearray(256 * 1024 * 1024); block[::4096] = b'x' * 65536"


class PeakApi:
    """The Win32 layer calls ``JobObject`` makes here; the peak is scripted."""

    def __init__(self):
        self.calls = []

    def create(self):
        return 77

    def set_kill_on_close(self, handle):
        self.calls.append(('set', handle))

    def peak_memory(self, handle):
        self.calls.append(('peak', handle))
        return 600 * MIB, 500 * MIB

    def close(self, handle):
        self.calls.append(('close', handle))


def test_job_object_passes_its_handle_and_returns_job_then_process_peak():
    api = PeakApi()
    job = JobObject.create(api)
    assert job.peak_memory() == (600 * MIB, 500 * MIB)
    assert api.calls[-1] == ('peak', 77)


def test_closed_job_object_refuses_peak_memory():
    api = PeakApi()
    job = JobObject.create(api)
    job.close()
    with pytest.raises(JobObjectError):
        job.peak_memory()
    assert ('peak', 77) not in api.calls


def test_extended_limit_class_is_nine():
    assert job_object._JOB_EXTENDED_LIMIT == 9   # JobObjectExtendedLimitInformation in JOBOBJECTINFOCLASS


@windows_only
@pytest.mark.skipif(sys.maxsize <= 2 ** 32, reason='64-bit layout')
def test_extended_limit_layout():
    ns = Win32JobApi()._load()
    extended = ns['ExtendedLimit']
    assert ctypes.sizeof(extended) == 144
    assert extended.IoInfo.offset == 64
    assert extended.ProcessMemoryLimit.offset == 112
    assert extended.PeakProcessMemoryUsed.offset == 128
    assert extended.PeakJobMemoryUsed.offset == 136


@windows_only
def test_an_empty_job_has_no_peak():
    job = JobObject.create()
    try:
        assert job.peak_memory() == (0, 0)
    finally:
        job.close()


@windows_only
def test_a_handle_that_is_not_a_job_raises():
    """-1 is the current-process pseudo handle (0 would query the job this process runs in)."""
    with pytest.raises(JobObjectError, match='ExtendedLimitInformation'):
        Win32JobApi().peak_memory(-1)


def _open_for_job(pid):
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    assert handle, ctypes.get_last_error()
    return k32, handle


@windows_only
def test_the_peak_survives_the_member_exit():
    job = JobObject.create()
    child = subprocess.Popen([sys.executable, '-c', CHILD], stdin=subprocess.PIPE)
    try:
        k32, handle = _open_for_job(child.pid)
        try:
            job.assign(handle)
        finally:
            k32.CloseHandle(handle)
        child.communicate(b'go\n', timeout=60)
        assert child.returncode == 0
        assert job.active_processes() == 0
        job_peak, process_peak = job.peak_memory()
        assert process_peak >= 256 * MIB
        assert job_peak >= process_peak
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        job.close()
