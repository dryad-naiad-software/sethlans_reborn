# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
PCI address of a DXGI adapter, by its LUID (Windows only).

``pci_address(luid)`` asks the kernel graphics interface in ``gdi32.dll``:
``D3DKMTOpenAdapterFromLuid``, ``D3DKMTQueryAdapterInfo`` with
``KMTQAITYPE_ADAPTERADDRESS`` and ``D3DKMTCloseAdapter`` (the handle is closed
in ``finally``). It runs in-process and needs no elevation. The result is the
normalised ``dddd:bb:dd.f`` text with domain 0, or ``None`` on any failure: a
missing DLL or export, a failing status, or an address out of range (the
software adapter reports all ones). No adapter-kind check is made.

``available()`` says whether the three exports can be loaded at all, so a
caller can tell "this adapter has no address" from "no adapter can have one".
"""
import ctypes
import logging
import sys
import threading
from typing import Optional, Tuple

from sethlans_worker_agent.gpu_memory.pci import normalize_pci

logger = logging.getLogger(__name__)

LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
KMTQAITYPE_ADAPTERADDRESS = 6


class LUID(ctypes.Structure):
    _fields_ = [('LowPart', ctypes.c_uint32), ('HighPart', ctypes.c_int32)]


class D3DKMT_OPENADAPTERFROMLUID(ctypes.Structure):
    _fields_ = [('AdapterLuid', LUID), ('hAdapter', ctypes.c_uint32)]


class D3DKMT_QUERYADAPTERINFO(ctypes.Structure):
    _fields_ = [('hAdapter', ctypes.c_uint32), ('Type', ctypes.c_int),
                ('pPrivateDriverData', ctypes.c_void_p), ('PrivateDriverDataSize', ctypes.c_uint32)]


class D3DKMT_ADAPTERADDRESS(ctypes.Structure):
    _fields_ = [('BusNumber', ctypes.c_uint32), ('DeviceNumber', ctypes.c_uint32),
                ('FunctionNumber', ctypes.c_uint32)]


class D3DKMT_CLOSEADAPTER(ctypes.Structure):
    _fields_ = [('hAdapter', ctypes.c_uint32)]


_api_lock = threading.Lock()
_api = None


class _Api:
    def __init__(self):
        dll = ctypes.WinDLL('gdi32.dll', winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
        self.open_adapter = dll.D3DKMTOpenAdapterFromLuid
        self.open_adapter.argtypes = [ctypes.POINTER(D3DKMT_OPENADAPTERFROMLUID)]
        self.open_adapter.restype = ctypes.c_long
        self.query_info = dll.D3DKMTQueryAdapterInfo
        self.query_info.argtypes = [ctypes.POINTER(D3DKMT_QUERYADAPTERINFO)]
        self.query_info.restype = ctypes.c_long
        self.close_adapter = dll.D3DKMTCloseAdapter
        self.close_adapter.argtypes = [ctypes.POINTER(D3DKMT_CLOSEADAPTER)]
        self.close_adapter.restype = ctypes.c_long


def _get_api() -> Optional[_Api]:
    """The bound exports, or ``None`` off Windows or when they cannot be loaded."""
    global _api
    if sys.platform != 'win32':
        return None
    with _api_lock:
        if _api is None:
            try:
                _api = _Api()
            except (OSError, AttributeError) as exc:
                logger.debug("D3DKMT functions unavailable: %s", exc)
                return None
        return _api


def available() -> bool:
    """True when the D3DKMT exports can be called on this machine."""
    return _get_api() is not None


def _query_address(api: _Api, handle: int) -> Optional[str]:
    address = D3DKMT_ADAPTERADDRESS()
    query = D3DKMT_QUERYADAPTERINFO(handle, KMTQAITYPE_ADAPTERADDRESS,
                                    ctypes.cast(ctypes.byref(address), ctypes.c_void_p),
                                    ctypes.sizeof(address))
    status = api.query_info(ctypes.byref(query))
    if status != 0:
        logger.debug("D3DKMTQueryAdapterInfo failed (0x%08X).", status & 0xFFFFFFFF)
        return None
    return normalize_pci((int(address.BusNumber), int(address.DeviceNumber), int(address.FunctionNumber)))


def pci_address(luid: Tuple[int, int]) -> Optional[str]:
    """``dddd:bb:dd.f`` of the adapter with this ``(HighPart, LowPart)`` LUID, or ``None``."""
    try:
        api = _get_api()
        if api is None:
            return None
        high, low = luid
        opened = D3DKMT_OPENADAPTERFROMLUID(LUID(low & 0xFFFFFFFF, high), 0)
        status = api.open_adapter(ctypes.byref(opened))
        if status != 0:
            logger.debug("D3DKMTOpenAdapterFromLuid failed (0x%08X).", status & 0xFFFFFFFF)
            return None
        try:
            return _query_address(api, opened.hAdapter)
        finally:
            api.close_adapter(ctypes.byref(D3DKMT_CLOSEADAPTER(opened.hAdapter)))
    except (OSError, TypeError, ValueError, ctypes.ArgumentError) as exc:
        logger.debug("PCI address of an adapter could not be read: %s", exc)
        return None
