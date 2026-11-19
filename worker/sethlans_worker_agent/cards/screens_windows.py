# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Which card drives a screen, on Windows (FR-CS-1, M9).

For each DXGI adapter (``gpu_memory.win_dxgi.enumerate_adapters`` with a
``visit``): ``IDXGIAdapter::EnumOutputs(i)`` until ``DXGI_ERROR_NOT_FOUND``
(at most ``MAX_OUTPUTS``), ``IDXGIOutput::GetDesc`` for ``AttachedToDesktop``
and ``Monitor``, and ``GetMonitorInfoW(Monitor)`` for ``MONITORINFOF_PRIMARY``.
``screen`` = any attached output; ``primary_screen`` = any attached output on
the primary monitor. Each adapter is tied to its card key through its PCI
address by LUID (``win_adapter_info.pci_address``, as FR-CI-2), within this
call; the LUID is never kept. No D3D device is created.

``read()`` returns ``{key: (screen, primary_screen)}`` for the adapters with
a card key, or ``None`` ("unknown for every card") on any failure of a card
adapter's outputs, when DXGI or D3DKMT is unavailable, and when the process
runs in session 0 (a Windows service: no interactive desktop, so an empty
output list would read as "no screen", V13). Adapters without a key (the
software adapter, virtual display adapters) are ignored. Two adapters with
one key give that key ``(None, None)``.

Bindings: private ``WinDLL`` instances loaded from System32 on first use,
``argtypes``/``restype`` declared, every output released in ``finally``.
Nothing runs at import, so the module imports on every platform.
"""
import ctypes
import logging
import threading
from typing import Dict, List, Optional, Tuple

from sethlans_worker_agent.cards.identity import card_key
from sethlans_worker_agent.cards.os_probe import log_once

logger = logging.getLogger(__name__)

LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
DXGI_ERROR_NOT_FOUND = 0x887A0002
MONITORINFOF_PRIMARY = 0x1
MAX_OUTPUTS = 16
SLOT_ENUM_OUTPUTS = 7      # IDXGIAdapter::EnumOutputs
SLOT_OUTPUT_GET_DESC = 7   # IDXGIOutput::GetDesc
SESSION_TEXT = ("The worker runs without an interactive desktop (Windows session 0); which graphics card drives a "
                "screen is shown as unknown.")


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [('DeviceName', ctypes.c_wchar * 32), ('DesktopCoordinates', RECT), ('AttachedToDesktop', ctypes.c_int),
                ('Rotation', ctypes.c_int), ('Monitor', ctypes.c_void_p)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint32), ('rcMonitor', RECT), ('rcWork', RECT), ('dwFlags', ctypes.c_uint32)]


_api_lock = threading.Lock()
_api = None


class _Api:
    """The user32/kernel32 functions and the COM prototypes, built once."""

    def __init__(self):
        user32 = ctypes.WinDLL('user32.dll', winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
        self.get_monitor_info = user32.GetMonitorInfoW
        self.get_monitor_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        self.get_monitor_info.restype = ctypes.c_int
        kernel32 = ctypes.WinDLL('kernel32.dll', winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
        self.current_process_id = kernel32.GetCurrentProcessId
        self.current_process_id.argtypes = []
        self.current_process_id.restype = ctypes.c_uint32
        self.session_of = kernel32.ProcessIdToSessionId
        self.session_of.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        self.session_of.restype = ctypes.c_int
        proto = ctypes.WINFUNCTYPE
        self.enum_outputs = proto(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))
        self.output_desc = proto(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(DXGI_OUTPUT_DESC))


def _get_api() -> _Api:
    global _api
    with _api_lock:
        if _api is None:
            _api = _Api()
        return _api


def _interactive(api) -> bool:
    """False in session 0 (a service) or when the session cannot be read."""
    session = ctypes.c_uint32()
    if not api.session_of(api.current_process_id(), ctypes.byref(session)):
        logger.debug("The session of the worker process could not be read.")
        return False
    return session.value != 0


def _list_outputs(api, adapter) -> Optional[List[Tuple[bool, Optional[int]]]]:
    """``(attached, monitor handle)`` per output of ``adapter``, or ``None`` when one cannot be read."""
    from sethlans_worker_agent.gpu_memory import win_dxgi
    enum_outputs = win_dxgi.com_method(api.enum_outputs, adapter, SLOT_ENUM_OUTPUTS)
    found = []
    for index in range(MAX_OUTPUTS):
        output = ctypes.c_void_p()
        hr = enum_outputs(adapter, index, ctypes.byref(output))
        if (hr & 0xFFFFFFFF) == DXGI_ERROR_NOT_FOUND:
            return found
        try:
            if hr < 0:
                logger.debug("DXGI output %d could not be listed (0x%08X).", index, hr & 0xFFFFFFFF)
                return None
            desc = DXGI_OUTPUT_DESC()
            if win_dxgi.com_method(api.output_desc, output, SLOT_OUTPUT_GET_DESC)(output, ctypes.byref(desc)) < 0:
                logger.debug("DXGI output %d gave no description.", index)
                return None
        finally:
            win_dxgi.release(output)
        found.append((bool(desc.AttachedToDesktop), desc.Monitor))
    return found


def _is_primary(api, monitor) -> Optional[bool]:
    """``MONITORINFOF_PRIMARY`` of ``monitor`` through ``GetMonitorInfoW``; ``None`` when it cannot be read."""
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not api.get_monitor_info(monitor, ctypes.byref(info)):
        logger.debug("GetMonitorInfoW failed for an attached output.")
        return None
    return bool(info.dwFlags & MONITORINFOF_PRIMARY)


def adapter_marks(api, adapter) -> Optional[Tuple[bool, bool]]:
    """``(screen, primary_screen)`` of one adapter, or ``None`` when its outputs cannot be read."""
    outputs = _list_outputs(api, adapter)
    if outputs is None:
        return None
    screen = primary = False
    for attached, monitor in outputs:
        if not attached:
            continue
        flag = _is_primary(api, monitor)
        if flag is None:
            return None
        screen, primary = True, primary or flag
    return screen, primary


def _by_key(adapters) -> Optional[Dict[str, Tuple[Optional[bool], Optional[bool]]]]:
    """Card key -> marks for the adapters with a PCI address; ``None`` when a card adapter could not be read."""
    from sethlans_worker_agent.gpu_memory import win_adapter_info
    from sethlans_worker_agent.gpu_memory.win_dxgi import DXGI_ADAPTER_FLAG_SOFTWARE
    marks: Dict[str, Tuple[Optional[bool], Optional[bool]]] = {}
    seen = set()
    for desc, found in adapters:
        if desc.flags & DXGI_ADAPTER_FLAG_SOFTWARE:
            continue
        key = card_key(win_adapter_info.pci_address(desc.luid))
        if key is None:
            continue
        if found is None:
            return None
        marks[key] = (None, None) if key in seen else found
        seen.add(key)
    return marks


def read() -> Optional[Dict[str, Tuple[Optional[bool], Optional[bool]]]]:
    """``{key: (screen, primary_screen)}``, or ``None`` when the marks cannot be read (see the module docstring)."""
    from sethlans_worker_agent.gpu_memory import win_adapter_info, win_dxgi
    api = _get_api()
    if not _interactive(api):
        log_once(logger, logging.INFO, SESSION_TEXT)
        return None
    adapters = win_dxgi.enumerate_adapters(visit=lambda adapter: adapter_marks(api, adapter))
    if adapters is None or not win_adapter_info.available():
        logger.debug("The DXGI outputs or the adapters' PCI addresses are unavailable.")
        return None
    return _by_key(adapters)
