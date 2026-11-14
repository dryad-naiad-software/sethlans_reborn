# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Windows session unlock detection via WM_WTSSESSION_CHANGE (FR-4c).

Creates a hidden message window on a dedicated daemon thread that
receives session change notifications from Windows. When a session
unlock event fires, it sets a threading.Event for the YieldMonitor
to consume.

All Win32 calls go through ``idle_detection.win32_api``, which declares
``argtypes``/``restype`` for every function (issue #234: an undeclared
``CreateWindowExW`` overflowed on the 64-bit ``hInstance``).

On non-Windows platforms this module is a no-op -- all public
functions return harmless defaults.
"""
import ctypes
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

CLASS_NAME = "SethlansSessionMonitor"

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
WS_EX_NOACTIVATE = 0x08000000
_HWND_MESSAGE = -3  # parent value that makes a message-only window


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


def _make_wnd_proc(api):
    """Return the ``WNDPROC`` callback for the session monitor window.

    The caller must keep the returned object alive for as long as the
    window class is registered, or ctypes frees the thunk under Windows.
    """
    def wnd_proc(hwnd, msg, wparam, lparam):
        try:
            if msg == WM_WTSSESSION_CHANGE and wparam == WTS_SESSION_UNLOCK:
                logger.info("Session unlock detected.")
                session_unlock_event.set()
            elif msg == WM_DESTROY:
                # Unregister before the window is gone, then end the pump.
                api.wtsapi32.WTSUnRegisterSessionNotification(hwnd)
                api.user32.PostQuitMessage(0)
                return 0
        except Exception:
            logger.exception("Session monitor window procedure failed.")
        return api.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    return api.WNDPROC(wnd_proc)


def _create_message_window(api, callback):
    """Register the window class and create the message-only window.

    Returns ``(hwnd, hinstance)``. Raises ``OSError`` when either call
    fails; the class is unregistered again if window creation fails.
    """
    hinstance = api.kernel32.GetModuleHandleW(None)

    wc = api.WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(api.WNDCLASSEXW)
    wc.lpfnWndProc = callback
    wc.hInstance = hinstance
    wc.lpszClassName = CLASS_NAME
    if not api.user32.RegisterClassExW(ctypes.byref(wc)):
        raise ctypes.WinError(ctypes.get_last_error())

    hwnd = api.user32.CreateWindowExW(
        WS_EX_NOACTIVATE,
        CLASS_NAME,
        CLASS_NAME,
        0,  # style
        0, 0, 0, 0,  # x, y, w, h
        _HWND_MESSAGE,  # message-only window
        None, hinstance, None,
    )
    if not hwnd:
        error = ctypes.get_last_error()
        api.user32.UnregisterClassW(CLASS_NAME, hinstance)
        raise ctypes.WinError(error)
    return hwnd, hinstance


def _destroy_message_window(api, hwnd, hinstance) -> None:
    """Destroy the window (if it still exists) and unregister its class.

    Must run on the thread that created the window.
    """
    if hwnd and api.user32.IsWindow(hwnd):
        api.user32.DestroyWindow(hwnd)
    api.user32.UnregisterClassW(CLASS_NAME, hinstance)


def _pump_messages(api) -> None:
    """Run GetMessage/DispatchMessage until WM_QUIT or an error."""
    msg = api.MSG()
    while api.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        api.user32.TranslateMessage(ctypes.byref(msg))
        api.user32.DispatchMessageW(ctypes.byref(msg))


def _win32_message_pump(on_ready=None) -> None:
    """Win32 hidden window + GetMessage/DispatchMessage pump.

    Runs on its daemon thread until the window is destroyed (which posts
    WM_QUIT). ``on_ready``, if given, is called with the HWND once the
    window exists and is registered for session notifications.
    """
    try:
        from sethlans_worker_agent.idle_detection import win32_api as api

        callback = _make_wnd_proc(api)
        hwnd, hinstance = _create_message_window(api, callback)
        try:
            if not api.wtsapi32.WTSRegisterSessionNotification(
                hwnd, NOTIFY_FOR_THIS_SESSION,
            ):
                logger.warning(
                    "WTSRegisterSessionNotification failed (error %d).",
                    ctypes.get_last_error(),
                )
            logger.debug("Session monitor message pump running.")
            if on_ready is not None:
                on_ready(hwnd)
            _pump_messages(api)
        finally:
            _destroy_message_window(api, hwnd, hinstance)
        logger.info("Windows session monitor stopped.")
    except Exception:
        logger.exception("Session monitor thread failed.")
