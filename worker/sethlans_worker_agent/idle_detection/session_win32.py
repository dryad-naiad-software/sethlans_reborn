# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Windows session unlock detection via WM_WTSSESSION_CHANGE (FR-4c).

Creates a hidden message window on a dedicated daemon thread that
receives session change notifications from Windows. When a session
unlock event fires, it sets a threading.Event for the YieldMonitor
to consume.

On non-Windows platforms this module is a no-op -- all public
functions return harmless defaults.
"""
import logging
import sys
import threading

logger = logging.getLogger(__name__)

# Public event: set when a session unlock is detected.
# The YieldMonitor checks and clears this each poll cycle.
session_unlock_event = threading.Event()

_pump_thread: threading.Thread = None
_started = False
_start_lock = threading.Lock()


def start_session_monitor() -> None:
    """Start the hidden window message pump on a daemon thread.

    Safe to call on any platform; no-op on non-Windows.
    Must be called once at worker startup. Subsequent calls are ignored.
    """
    global _started, _pump_thread

    if sys.platform != "win32":
        return

    with _start_lock:
        if _started:
            return
        _started = True
        _pump_thread = threading.Thread(
            target=_win32_message_pump,
            name="session-monitor",
            daemon=True,
        )
        _pump_thread.start()
        logger.info("Windows session monitor started.")


def _win32_message_pump() -> None:
    """Win32 hidden window + GetMessage/DispatchMessage pump.

    Runs forever on its daemon thread. Creates a hidden HWND,
    registers for session notifications, and dispatches messages.
    """
    try:
        import ctypes
        import ctypes.wintypes

        # Constants
        WM_WTSSESSION_CHANGE = 0x02B1
        WTS_SESSION_UNLOCK = 0x8
        NOTIFY_FOR_THIS_SESSION = 0
        WS_EX_NOACTIVATE = 0x08000000
        HWND_MESSAGE = ctypes.wintypes.HWND(-3)

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        wtsapi32 = ctypes.windll.wtsapi32

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.wintypes.HWND,
            ctypes.c_uint,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_WTSSESSION_CHANGE and wparam == WTS_SESSION_UNLOCK:
                logger.info("Session unlock detected.")
                session_unlock_event.set()
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        # prevent garbage collection of the callback
        callback = WNDPROC(wnd_proc)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.wintypes.HINSTANCE),
                ("hIcon", ctypes.wintypes.HICON),
                ("hCursor", ctypes.wintypes.HANDLE),
                ("hbrBackground", ctypes.wintypes.HANDLE),
                ("lpszMenuName", ctypes.wintypes.LPCWSTR),
                ("lpszClassName", ctypes.wintypes.LPCWSTR),
                ("hIconSm", ctypes.wintypes.HICON),
            ]

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = callback
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "SethlansSessionMonitor"

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            logger.error("RegisterClassExW failed for session monitor.")
            return

        hwnd = user32.CreateWindowExW(
            WS_EX_NOACTIVATE,
            wc.lpszClassName,
            "SethlansSessionMonitor",
            0,  # style
            0, 0, 0, 0,  # x, y, w, h
            HWND_MESSAGE,  # message-only window
            None, wc.hInstance, None,
        )
        if not hwnd:
            logger.error("CreateWindowExW failed for session monitor.")
            return

        wtsapi32.WTSRegisterSessionNotification(
            hwnd, NOTIFY_FOR_THIS_SESSION,
        )

        logger.debug("Session monitor message pump running.")

        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    except Exception:
        logger.exception("Session monitor thread failed.")
