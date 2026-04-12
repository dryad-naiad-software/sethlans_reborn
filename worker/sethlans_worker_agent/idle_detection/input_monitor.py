# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-platform input idle time detection (FR-1).

Provides ``get_seconds_since_last_input()`` which dispatches to the
correct platform implementation:
- Windows: GetLastInputInfo + GetTickCount64 (avoids 49-day wraparound)
- macOS: CGEventSourceSecondsSinceLastEventType via CoreGraphics
- Linux X11: XScreenSaverQueryInfo via libXss (under _xlib_lock)
- Linux Wayland: busctl IdleHint via subprocess (boolean only)

Returns None if the platform API is unavailable.
"""
import logging
import os
import subprocess
import sys
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level lock for Linux X11 Xlib thread safety (FR-1c).
# All Xlib calls (XOpenDisplay, XScreenSaverQueryInfo, XCloseDisplay)
# are serialized through this lock. Preferred over XInitThreads() to
# avoid global state mutation.
_xlib_lock = threading.Lock()


def _get_idle_seconds_windows() -> Optional[float]:
    """Windows: GetLastInputInfo + GetTickCount64."""
    try:
        import ctypes
        import ctypes.wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.UINT),
                ("dwTime", ctypes.wintypes.DWORD),
            ]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            logger.debug("GetLastInputInfo failed.")
            return None

        # Use GetTickCount64 (not GetTickCount) to avoid 49-day
        # DWORD wraparound (FR-1a).
        tick64 = ctypes.windll.kernel32.GetTickCount64
        tick64.restype = ctypes.c_uint64
        current_tick = tick64()
        elapsed_ms = current_tick - lii.dwTime
        return elapsed_ms / 1000.0
    except Exception as exc:
        logger.debug("Windows idle detection unavailable: %s", exc)
        return None


def _get_idle_seconds_macos() -> Optional[float]:
    """macOS: CGEventSourceSecondsSinceLastEventType via CoreGraphics."""
    try:
        import ctypes
        import ctypes.util

        cg_path = ctypes.util.find_library("CoreGraphics")
        if not cg_path:
            return None
        cg = ctypes.cdll.LoadLibrary(cg_path)

        # kCGEventSourceStateCombinedSessionState = 0
        # kCGAnyInputEventType = ~0 (0xFFFFFFFF)
        fn = cg.CGEventSourceSecondsSinceLastEventType
        fn.restype = ctypes.c_double
        fn.argtypes = [ctypes.c_int32, ctypes.c_uint32]
        seconds = fn(0, 0xFFFFFFFF)
        return float(seconds)
    except Exception as exc:
        logger.debug("macOS idle detection unavailable: %s", exc)
        return None


def _get_idle_seconds_linux_x11() -> Optional[float]:
    """Linux X11: XScreenSaverQueryInfo via libXss."""
    display_env = os.environ.get("DISPLAY")
    if not display_env:
        return None

    try:
        import ctypes
        import ctypes.util

        xss_path = ctypes.util.find_library("Xss")
        xlib_path = ctypes.util.find_library("X11")
        if not xss_path or not xlib_path:
            return None

        xss = ctypes.cdll.LoadLibrary(xss_path)
        xlib = ctypes.cdll.LoadLibrary(xlib_path)

        class XScreenSaverInfo(ctypes.Structure):
            _fields_ = [
                ("window", ctypes.c_ulong),
                ("state", ctypes.c_int),
                ("kind", ctypes.c_int),
                ("since", ctypes.c_ulong),
                ("idle", ctypes.c_ulong),
                ("event_mask", ctypes.c_ulong),
            ]

        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]

        xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(
            XScreenSaverInfo
        )
        xss.XScreenSaverQueryInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(XScreenSaverInfo),
        ]
        xss.XScreenSaverQueryInfo.restype = ctypes.c_int

        with _xlib_lock:
            display = xlib.XOpenDisplay(None)
            if not display:
                return None
            try:
                root = xlib.XDefaultRootWindow(display)
                info = xss.XScreenSaverAllocInfo()
                if not info:
                    return None
                status = xss.XScreenSaverQueryInfo(display, root, info)
                if status == 0:
                    return None
                idle_ms = info.contents.idle
                return idle_ms / 1000.0
            finally:
                xlib.XCloseDisplay(display)
    except Exception as exc:
        logger.debug("Linux X11 idle detection unavailable: %s", exc)
        return None


def _get_idle_seconds_linux_dbus() -> Optional[float]:
    """Linux Wayland fallback: busctl IdleHint.

    Returns 0.0 if the session is NOT idle (user active),
    or float('inf') if IdleHint reports idle. This is a boolean
    signal, not a precise duration.
    """
    try:
        result = subprocess.run(
            [
                "busctl", "get-property",
                "org.freedesktop.login1",
                "/org/freedesktop/login1/session/auto",
                "org.freedesktop.login1.Session",
                "IdleHint",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        # Output format: "b true" or "b false"
        if output.endswith("true"):
            return float("inf")
        elif output.endswith("false"):
            return 0.0
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("Linux D-Bus idle detection unavailable: %s", exc)
        return None


def get_seconds_since_last_input() -> Optional[float]:
    """Return seconds since last keyboard/mouse input, or None if unavailable.

    Dispatches to the correct platform implementation.
    """
    if sys.platform == "win32":
        return _get_idle_seconds_windows()
    elif sys.platform == "darwin":
        return _get_idle_seconds_macos()
    elif sys.platform.startswith("linux"):
        # Try X11 first, fall back to D-Bus
        result = _get_idle_seconds_linux_x11()
        if result is not None:
            return result
        return _get_idle_seconds_linux_dbus()
    else:
        logger.warning(
            "Unsupported platform %r for input idle detection.",
            sys.platform,
        )
        return None
