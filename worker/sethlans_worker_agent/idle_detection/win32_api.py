# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Typed Win32 bindings for the idle-detection package (issue #234).

Every user32 / kernel32 / wtsapi32 function the package calls is declared
here, once, at import time, with explicit ``argtypes`` and ``restype``.
Without them ctypes passes each Python int as a C ``int`` and returns a C
``int``, so a 64-bit handle (``HINSTANCE``, ``HWND``) overflows or is
truncated on 64-bit Windows.

The DLLs are private ``ctypes.WinDLL`` instances rather than the shared
``ctypes.windll.<dll>`` objects. ``windll`` caches one function object per
name for the whole process, so setting ``argtypes`` there would clash with
any other module that declares the same function differently.

Windows only: importing this module elsewhere raises ``ImportError``.
Callers import it lazily, inside their Windows code paths.
"""
import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - guarded by callers
    raise ImportError("win32_api is only available on Windows")

# Pointer-sized integer types: LRESULT and LPARAM are LONG_PTR, WPARAM is UINT_PTR.
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

# LRESULT CALLBACK WindowProc(HWND, UINT, WPARAM, LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    """``WNDCLASSEXW`` from winuser.h."""

    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class LASTINPUTINFO(ctypes.Structure):
    """``LASTINPUTINFO`` from winuser.h."""

    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

MSG = wintypes.MSG
_PMSG = ctypes.POINTER(MSG)

# (dll, function name, argtypes, restype)
SIGNATURES = (
    (kernel32, "GetModuleHandleW", [wintypes.LPCWSTR], wintypes.HMODULE),
    (kernel32, "GetTickCount64", [], ctypes.c_uint64),
    (user32, "RegisterClassExW", [ctypes.POINTER(WNDCLASSEXW)], wintypes.ATOM),
    (user32, "UnregisterClassW", [wintypes.LPCWSTR, wintypes.HINSTANCE], wintypes.BOOL),
    (user32, "CreateWindowExW", [
        wintypes.DWORD,      # dwExStyle
        wintypes.LPCWSTR,    # lpClassName
        wintypes.LPCWSTR,    # lpWindowName
        wintypes.DWORD,      # dwStyle
        ctypes.c_int,        # X
        ctypes.c_int,        # Y
        ctypes.c_int,        # nWidth
        ctypes.c_int,        # nHeight
        wintypes.HWND,       # hWndParent
        wintypes.HMENU,      # hMenu
        wintypes.HINSTANCE,  # hInstance
        wintypes.LPVOID,     # lpParam
    ], wintypes.HWND),
    (user32, "DestroyWindow", [wintypes.HWND], wintypes.BOOL),
    (user32, "IsWindow", [wintypes.HWND], wintypes.BOOL),
    (user32, "DefWindowProcW", [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM], LRESULT),
    (user32, "PostMessageW", [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM], wintypes.BOOL),
    (user32, "PostQuitMessage", [ctypes.c_int], None),
    (user32, "GetMessageW", [_PMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
    (user32, "TranslateMessage", [_PMSG], wintypes.BOOL),
    (user32, "DispatchMessageW", [_PMSG], LRESULT),
    (user32, "GetLastInputInfo", [ctypes.POINTER(LASTINPUTINFO)], wintypes.BOOL),
    (wtsapi32, "WTSRegisterSessionNotification", [wintypes.HWND, wintypes.DWORD], wintypes.BOOL),
    (wtsapi32, "WTSUnRegisterSessionNotification", [wintypes.HWND], wintypes.BOOL),
)

for _dll, _name, _argtypes, _restype in SIGNATURES:
    _fn = getattr(_dll, _name)
    _fn.argtypes = _argtypes
    _fn.restype = _restype
del _dll, _name, _argtypes, _restype, _fn
