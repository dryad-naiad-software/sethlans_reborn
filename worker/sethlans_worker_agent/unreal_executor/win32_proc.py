# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Small Win32 helpers for Unreal process identity (Unreal foundation
FR-UE-23, FR-UE-26, FR-UE-27). ctypes only; ``pywin32`` is not used (#214).

* ``process_owner_sid(pid)``: the SID string of a process's token user
  (``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` ->
  ``OpenProcessToken`` -> ``GetTokenInformation(TokenUser)``). Raises
  ``OSError`` when it cannot be read (access denied, process gone).
* ``long_path_name(path)``: ``GetLongPathNameW`` (expands 8.3 names). An
  input that does not exist (or any failure) is returned unchanged.

DLLs and ``ctypes.wintypes`` load on first use, so the module imports on
every platform; the functions raise ``OSError`` off Windows.
"""
import sys
import threading

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1

_api_cache = {}
_api_lock = threading.Lock()  # leaf lock


def _api():
    with _api_lock:
        if _api_cache:
            return _api_cache['ns']
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        vp, dword, boolean = ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL
        sigs = (
            (kernel32.OpenProcess, [dword, boolean, dword], wintypes.HANDLE),
            (kernel32.CloseHandle, [wintypes.HANDLE], boolean),
            (kernel32.LocalFree, [vp], vp),
            (kernel32.GetLongPathNameW, [wintypes.LPCWSTR, wintypes.LPWSTR, dword], dword),
            (advapi32.OpenProcessToken, [wintypes.HANDLE, dword, ctypes.POINTER(wintypes.HANDLE)], boolean),
            (advapi32.GetTokenInformation, [wintypes.HANDLE, ctypes.c_int, vp, dword, ctypes.POINTER(dword)],
             boolean),
            (advapi32.ConvertSidToStringSidW, [vp, ctypes.POINTER(vp)], boolean),
        )
        for func, argtypes, restype in sigs:
            func.argtypes, func.restype = argtypes, restype
        ns = {'ctypes': ctypes, 'wintypes': wintypes, 'kernel32': kernel32, 'advapi32': advapi32}
        _api_cache['ns'] = ns
        return ns


def _error(api, what: str) -> OSError:
    code = api['ctypes'].get_last_error()
    return OSError(code, f"{what} failed (Windows error {code})")


def _token_user_sid(api, token) -> str:
    ctypes, wintypes, advapi32 = api['ctypes'], api['wintypes'], api['advapi32']
    size = wintypes.DWORD(0)
    advapi32.GetTokenInformation(token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(max(size.value, 1))
    if not advapi32.GetTokenInformation(token, _TOKEN_USER_CLASS, buf, size, ctypes.byref(size)):
        raise _error(api, 'GetTokenInformation')
    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    out = ctypes.c_void_p()
    if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)):
        raise _error(api, 'ConvertSidToStringSidW')
    try:
        return ctypes.wstring_at(out.value)
    finally:
        api['kernel32'].LocalFree(out)


def process_owner_sid(pid: int) -> str:
    """SID string of ``pid``'s token user; raises ``OSError`` when unreadable."""
    if sys.platform != 'win32':
        raise OSError("process_owner_sid is only available on Windows")
    api = _api()
    ctypes, wintypes, kernel32 = api['ctypes'], api['wintypes'], api['kernel32']
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not process:
        raise _error(api, 'OpenProcess')
    try:
        token = wintypes.HANDLE()
        if not api['advapi32'].OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
            raise _error(api, 'OpenProcessToken')
        try:
            return _token_user_sid(api, token)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(process)


def long_path_name(path: str) -> str:
    """``GetLongPathNameW(path)``; the input unchanged on any failure or off Windows."""
    if sys.platform != 'win32' or not path:
        return path
    try:
        api = _api()
        ctypes = api['ctypes']
        size = api['kernel32'].GetLongPathNameW(path, None, 0)
        if not size:
            return path
        buf = ctypes.create_unicode_buffer(size + 1)
        written = api['kernel32'].GetLongPathNameW(path, buf, size + 1)
        return buf.value if 0 < written <= size else path
    except (OSError, ValueError):
        return path
