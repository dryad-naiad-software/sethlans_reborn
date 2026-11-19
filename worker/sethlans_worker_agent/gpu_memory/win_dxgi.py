# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
DXGI adapter list (Windows only).

``enumerate_adapters()`` lists every DXGI adapter in the order Unreal
indexes ``-graphicsadapter`` by: ``IDXGIFactory6::EnumAdapterByGpuPreference``
with ``DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE``, or ``IDXGIFactory1::EnumAdapters1``
only when the factory has no ``IDXGIFactory6``. The index of each adapter is
the raw loop index in that order, counting every adapter (the software
adapter and virtual adapters included).

Rules: a private ``WinDLL`` loaded from System32 on first use; every function
declared with ``argtypes``/``restype``; COM calls through ``WINFUNCTYPE``
prototypes bound to vtable slots; ``Release`` on every interface obtained, in
``finally``. No ``CoInitialize`` (``CreateDXGIFactory1`` is not a COM
activation). Any failure other than ``DXGI_ERROR_NOT_FOUND`` ending the list
makes the whole call return ``None``; it never raises.

Nothing here runs at import, so the module imports on every platform.
"""
import ctypes
import logging
import sys
import threading
import uuid
from typing import NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ADAPTER_FLAG_SOFTWARE = 0x2
DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE = 2
MAX_ADAPTERS = 64

# vtable slots
SLOT_QUERY_INTERFACE = 0
SLOT_RELEASE = 2
SLOT_ENUM_ADAPTERS1 = 12           # IDXGIFactory1
SLOT_GET_DESC1 = 10                # IDXGIAdapter1
SLOT_ENUM_BY_GPU_PREFERENCE = 29   # IDXGIFactory6


class GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


IID_IDXGIFactory1 = _guid('770aae78-f26f-4dba-a829-253c83d1b387')
IID_IDXGIFactory6 = _guid('c1b6694f-ff09-44a9-b03c-77900a0a1d17')
IID_IDXGIAdapter1 = _guid('29038f61-3839-4626-91fd-086879011a05')


class LUID(ctypes.Structure):
    _fields_ = [('LowPart', ctypes.c_uint32), ('HighPart', ctypes.c_int32)]


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [('Description', ctypes.c_wchar * 128), ('VendorId', ctypes.c_uint32),
                ('DeviceId', ctypes.c_uint32), ('SubSysId', ctypes.c_uint32), ('Revision', ctypes.c_uint32),
                ('DedicatedVideoMemory', ctypes.c_size_t), ('DedicatedSystemMemory', ctypes.c_size_t),
                ('SharedSystemMemory', ctypes.c_size_t), ('AdapterLuid', LUID), ('Flags', ctypes.c_uint32)]


class AdapterDesc(NamedTuple):
    index: int                  # raw loop index in the enumeration order
    name: str
    vendor_id: int
    device_id: int
    dedicated_bytes: int
    luid: Tuple[int, int]       # (HighPart, LowPart); valid within one boot only
    flags: int
    revision: Optional[int] = None  # PCI revision id (DXGI_ADAPTER_DESC1.Revision)


_api_lock = threading.Lock()
_api = None


class _Api:
    """The DLL function and the COM prototypes, built once."""

    def __init__(self):
        dll = ctypes.WinDLL('dxgi.dll', winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
        self.create_factory1 = dll.CreateDXGIFactory1
        self.create_factory1.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.create_factory1.restype = ctypes.c_long
        proto = ctypes.WINFUNCTYPE
        pp_void = ctypes.POINTER(ctypes.c_void_p)
        self.query_interface = proto(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), pp_void)
        self.release = proto(ctypes.c_ulong, ctypes.c_void_p)
        self.enum_adapters1 = proto(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32, pp_void)
        self.enum_by_preference = proto(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                        ctypes.POINTER(GUID), pp_void)
        self.get_desc1 = proto(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(DXGI_ADAPTER_DESC1))


def _get_api() -> _Api:
    global _api
    with _api_lock:
        if _api is None:
            _api = _Api()
        return _api


def _method(prototype, obj: ctypes.c_void_p, slot: int):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return prototype(vtable[slot])


def _release(api: _Api, obj: Optional[ctypes.c_void_p]) -> None:
    if obj is not None and obj.value:
        _method(api.release, obj, SLOT_RELEASE)(obj)


def _failed(hr: int) -> bool:
    return hr < 0


def _read_desc(api: _Api, adapter: ctypes.c_void_p, index: int) -> Optional[AdapterDesc]:
    desc = DXGI_ADAPTER_DESC1()
    if _failed(_method(api.get_desc1, adapter, SLOT_GET_DESC1)(adapter, ctypes.byref(desc))):
        return None
    luid = (int(desc.AdapterLuid.HighPart), int(desc.AdapterLuid.LowPart))
    return AdapterDesc(index, desc.Description, int(desc.VendorId), int(desc.DeviceId),
                       int(desc.DedicatedVideoMemory), luid, int(desc.Flags), int(desc.Revision))


def _next_adapter(api, factory, factory6, index, adapter) -> int:
    if factory6 is not None:
        call = _method(api.enum_by_preference, factory6, SLOT_ENUM_BY_GPU_PREFERENCE)
        return call(factory6, index, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
                    ctypes.byref(IID_IDXGIAdapter1), ctypes.byref(adapter))
    return _method(api.enum_adapters1, factory, SLOT_ENUM_ADAPTERS1)(factory, index, ctypes.byref(adapter))


def _walk(api: _Api, factory, factory6) -> Optional[Tuple[AdapterDesc, ...]]:
    found = []
    for index in range(MAX_ADAPTERS):
        adapter = ctypes.c_void_p()
        hr = _next_adapter(api, factory, factory6, index, adapter)
        if (hr & 0xFFFFFFFF) == DXGI_ERROR_NOT_FOUND:
            break
        try:
            if _failed(hr):
                logger.debug("DXGI adapter %d could not be listed (0x%08X).", index, hr & 0xFFFFFFFF)
                return None
            desc = _read_desc(api, adapter, index)
        finally:
            _release(api, adapter)
        if desc is None:
            logger.debug("DXGI adapter %d gave no description.", index)
            return None
        found.append(desc)
    return tuple(found)


def _enumerate(api: _Api) -> Optional[Tuple[AdapterDesc, ...]]:
    factory = ctypes.c_void_p()
    hr = api.create_factory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory))
    if _failed(hr):
        logger.debug("CreateDXGIFactory1 failed (0x%08X).", hr & 0xFFFFFFFF)
        return None
    factory6 = ctypes.c_void_p()
    try:
        query = _method(api.query_interface, factory, SLOT_QUERY_INTERFACE)
        if _failed(query(factory, ctypes.byref(IID_IDXGIFactory6), ctypes.byref(factory6))):
            logger.debug("IDXGIFactory6 is not available; listing adapters with EnumAdapters1.")
            factory6 = ctypes.c_void_p()
        return _walk(api, factory, factory6 if factory6.value else None)
    finally:
        _release(api, factory6)
        _release(api, factory)


def enumerate_adapters() -> Optional[Tuple[AdapterDesc, ...]]:
    """Every DXGI adapter in Unreal's ``-graphicsadapter`` order, or ``None`` on any failure."""
    if sys.platform != 'win32':
        return None
    try:
        api = _get_api()
        return _enumerate(api)
    except (OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError) as exc:  # missing DLL/export
        logger.debug("DXGI adapter list unavailable: %s", exc)
        return None
