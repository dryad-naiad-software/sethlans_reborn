# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Windows handles for the packager's containment checks (Unreal foundation
FR-PK-5, FR-PK-10). Imported on Windows only.

* ``open_file`` opens a file with ``FILE_FLAG_OPEN_REPARSE_POINT`` (the
  reparse point itself, never its target) and full sharing, and returns a
  CRT descriptor.
* ``open_dir`` opens a directory the same way **without**
  ``FILE_SHARE_DELETE``: while the handle is held, neither the directory
  nor any of its ancestors can be renamed or deleted, so a listing made
  through its path cannot be redirected.
* ``final_path`` is ``GetFinalPathNameByHandleW`` (normalized, DOS volume
  name, ``\\\\?\\`` form): where the opened object really is.
"""
import ctypes
import msvcrt
import os
from ctypes import wintypes

GENERIC_READ = 0x80000000
FILE_LIST_DIRECTORY = 0x1
FILE_READ_ATTRIBUTES = 0x80
SHARE_READ_WRITE = 0x1 | 0x2
SHARE_ALL = SHARE_READ_WRITE | 0x4
OPEN_EXISTING = 3
FLAG_BACKUP_SEMANTICS = 0x02000000
FLAG_OPEN_REPARSE_POINT = 0x00200000
FLAG_SEQUENTIAL_SCAN = 0x08000000
FILE_ATTRIBUTE_DIRECTORY = 0x10
_FINAL_PATH_FLAGS = 0  # FILE_NAME_NORMALIZED | VOLUME_NAME_DOS
_BUFFER_CHARS = 32768
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ('dwFileAttributes', wintypes.DWORD),
        ('ftCreationTime', wintypes.FILETIME),
        ('ftLastAccessTime', wintypes.FILETIME),
        ('ftLastWriteTime', wintypes.FILETIME),
        ('dwVolumeSerialNumber', wintypes.DWORD),
        ('nFileSizeHigh', wintypes.DWORD),
        ('nFileSizeLow', wintypes.DWORD),
        ('nNumberOfLinks', wintypes.DWORD),
        ('nFileIndexHigh', wintypes.DWORD),
        ('nFileIndexLow', wintypes.DWORD),
    ]


_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_CreateFileW = _kernel32.CreateFileW
_CreateFileW.restype = wintypes.HANDLE
_CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = (wintypes.HANDLE,)
_GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
_GetFinalPathNameByHandleW.restype = wintypes.DWORD
_GetFinalPathNameByHandleW.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
_GetFileInformationByHandle = _kernel32.GetFileInformationByHandle
_GetFileInformationByHandle.restype = wintypes.BOOL
_GetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation))


def _create(path: str, access: int, share: int, flags: int) -> int:
    handle = _CreateFileW(path, access, share, None, OPEN_EXISTING, flags, None)
    if handle is None or handle == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def close(handle: int) -> None:
    _CloseHandle(handle)


def open_file(path: str) -> int:
    """A read-only CRT descriptor for the file at ``path`` (the reparse point itself if it is one)."""
    handle = _create(path, GENERIC_READ, SHARE_ALL, FLAG_OPEN_REPARSE_POINT | FLAG_SEQUENTIAL_SCAN)
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise


def open_dir(path: str) -> int:
    """A handle on the directory at ``path`` that blocks renaming it or its ancestors."""
    return _create(path, FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES, SHARE_READ_WRITE,
                   FLAG_BACKUP_SEMANTICS | FLAG_OPEN_REPARSE_POINT)


def attributes(handle: int) -> int:
    info = _ByHandleFileInformation()
    if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.dwFileAttributes


def final_path(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(_BUFFER_CHARS)
    length = _GetFinalPathNameByHandleW(handle, buffer, _BUFFER_CHARS, _FINAL_PATH_FLAGS)
    if not length or length >= _BUFFER_CHARS:
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def fd_final_path(fd: int) -> str:
    return final_path(msvcrt.get_osfhandle(fd))


def same_path(actual: str, expected: str) -> bool:
    """Equal after ``normcase`` and dropping a trailing separator (``\\\\?\\`` forms on both sides)."""
    return os.path.normcase(actual).rstrip('\\') == os.path.normcase(expected).rstrip('\\')
