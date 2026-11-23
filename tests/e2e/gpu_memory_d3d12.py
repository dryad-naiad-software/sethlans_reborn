# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
GPU memory held through Direct3D 12 on one adapter (Windows only), for
``gpu_memory_holder``. Only what Windows itself has: ``d3d12.dll`` through
ctypes, no vendor library.

``D3D12Memory(luid)`` opens a device on the DXGI adapter whose LUID is
``luid`` (the worker's ``win_dxgi.enumerate_adapters`` lists the adapters;
the device is created on the matching one only) and checks the device's own
``GetAdapterLuid`` against it; ``allocate(nbytes)`` makes one committed
buffer resource in a DEFAULT heap (video memory, resident at creation);
``free()`` releases every resource, then the device.

Names and values checked against the Windows SDK 10.0.26100.0 headers
(``um/d3d12.h``, ``um/d3dcommon.h``): ``D3D12CreateDevice(IUnknown*,
D3D_FEATURE_LEVEL, REFIID, void**)``; ``ID3D12Device`` vtable slot 27
``CreateCommittedResource(const D3D12_HEAP_PROPERTIES*, D3D12_HEAP_FLAGS,
const D3D12_RESOURCE_DESC*, D3D12_RESOURCE_STATES, const D3D12_CLEAR_VALUE*,
REFIID, void**)`` and slot 43 ``GetAdapterLuid`` (C form on ``_WIN32``:
``LUID* (This, LUID* RetVal)``); ``D3D12_HEAP_TYPE_DEFAULT`` 1,
``D3D12_RESOURCE_DIMENSION_BUFFER`` 1, ``D3D12_TEXTURE_LAYOUT_ROW_MAJOR`` 1,
``D3D12_RESOURCE_STATE_COMMON`` 0, ``D3D12_HEAP_FLAG_NONE`` 0,
``D3D_FEATURE_LEVEL_11_0`` 0xb000; ``IID_ID3D12Device``
189819f1-1db6-4b57-be54-1821339b85f7, ``IID_ID3D12Resource``
696442be-a72e-4059-bc79-5b5c98040fad.
"""

import ctypes
import uuid

from sethlans_worker_agent.gpu_memory import win_dxgi

D3D_FEATURE_LEVEL_11_0 = 0xB000
D3D12_HEAP_TYPE_DEFAULT = 1
D3D12_RESOURCE_DIMENSION_BUFFER = 1
D3D12_TEXTURE_LAYOUT_ROW_MAJOR = 1
D3D12_RESOURCE_STATE_COMMON = 0
D3D12_HEAP_FLAG_NONE = 0
SLOT_CREATE_COMMITTED_RESOURCE = 27
SLOT_GET_ADAPTER_LUID = 43


def _guid(text):
    return win_dxgi.GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


IID_ID3D12Device = _guid("189819f1-1db6-4b57-be54-1821339b85f7")
IID_ID3D12Resource = _guid("696442be-a72e-4059-bc79-5b5c98040fad")


class D3D12_HEAP_PROPERTIES(ctypes.Structure):
    _fields_ = [("Type", ctypes.c_int), ("CPUPageProperty", ctypes.c_int), ("MemoryPoolPreference", ctypes.c_int),
                ("CreationNodeMask", ctypes.c_uint32), ("VisibleNodeMask", ctypes.c_uint32)]


class DXGI_SAMPLE_DESC(ctypes.Structure):
    _fields_ = [("Count", ctypes.c_uint32), ("Quality", ctypes.c_uint32)]


class D3D12_RESOURCE_DESC(ctypes.Structure):
    _fields_ = [("Dimension", ctypes.c_int), ("Alignment", ctypes.c_uint64), ("Width", ctypes.c_uint64),
                ("Height", ctypes.c_uint32), ("DepthOrArraySize", ctypes.c_uint16), ("MipLevels", ctypes.c_uint16),
                ("Format", ctypes.c_int), ("SampleDesc", DXGI_SAMPLE_DESC), ("Layout", ctypes.c_int),
                ("Flags", ctypes.c_int)]


class D3D12Error(RuntimeError):
    """A Direct3D 12 call failed, or the adapter could not be matched."""


_PP = ctypes.POINTER(ctypes.c_void_p)
_GET_DESC1 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(win_dxgi.DXGI_ADAPTER_DESC1))
_CREATE_COMMITTED = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(D3D12_HEAP_PROPERTIES), ctypes.c_int,
    ctypes.POINTER(D3D12_RESOURCE_DESC), ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(win_dxgi.GUID), _PP)
_GET_ADAPTER_LUID = ctypes.WINFUNCTYPE(ctypes.POINTER(win_dxgi.LUID), ctypes.c_void_p,
                                       ctypes.POINTER(win_dxgi.LUID))


def _hex(hr):
    return f"0x{hr & 0xFFFFFFFF:08X}"


def _create_device_fn():
    dll = ctypes.WinDLL("d3d12.dll", winmode=win_dxgi.LOAD_LIBRARY_SEARCH_SYSTEM32)
    create = dll.D3D12CreateDevice
    create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(win_dxgi.GUID), _PP]
    create.restype = ctypes.c_long
    return create


class D3D12Memory:
    """Committed DEFAULT-heap buffers on the adapter with one LUID; ``free()`` releases them all."""

    def __init__(self, luid):
        self.luid = tuple(luid)  # (HighPart, LowPart), as win_dxgi gives it
        self.device = None
        self.resources = []

    def _visit(self, create, found):
        """``enumerate_adapters`` visit: a device on the adapter whose LUID matches, nothing on any other."""
        def visit(adapter):
            desc = win_dxgi.DXGI_ADAPTER_DESC1()
            if win_dxgi.com_method(_GET_DESC1, adapter, win_dxgi.SLOT_GET_DESC1)(adapter, ctypes.byref(desc)) < 0:
                return None
            if (int(desc.AdapterLuid.HighPart), int(desc.AdapterLuid.LowPart)) != self.luid:
                return None
            device = ctypes.c_void_p()
            hr = create(adapter, D3D_FEATURE_LEVEL_11_0, ctypes.byref(IID_ID3D12Device), ctypes.byref(device))
            found.append((hr, device))
            return hr
        return visit

    def open(self):
        """The device on the adapter; raises ``D3D12Error`` when it cannot be made or is on another adapter."""
        create, found = _create_device_fn(), []
        listed = win_dxgi.enumerate_adapters(self._visit(create, found))
        devices = [device for hr, device in found if hr >= 0 and device.value]
        if listed is None or len(found) != 1 or len(devices) != 1:
            for device in devices:
                win_dxgi.release(device)
            raise D3D12Error(f"no single D3D12 device on the adapter (listed: {listed is not None}, "
                             f"matches: {[_hex(hr) for hr, _d in found]})")
        self.device = devices[0]
        luid = win_dxgi.LUID()
        win_dxgi.com_method(_GET_ADAPTER_LUID, self.device, SLOT_GET_ADAPTER_LUID)(self.device, ctypes.byref(luid))
        if (int(luid.HighPart), int(luid.LowPart)) != self.luid:
            self.free()
            raise D3D12Error("the D3D12 device is not on the requested adapter")

    def allocate(self, nbytes):
        """One committed buffer of ``nbytes`` in a DEFAULT heap; raises ``D3D12Error`` on failure."""
        heap = D3D12_HEAP_PROPERTIES(D3D12_HEAP_TYPE_DEFAULT, 0, 0, 0, 0)
        desc = D3D12_RESOURCE_DESC(D3D12_RESOURCE_DIMENSION_BUFFER, 0, int(nbytes), 1, 1, 1, 0,
                                   DXGI_SAMPLE_DESC(1, 0), D3D12_TEXTURE_LAYOUT_ROW_MAJOR, 0)
        resource = ctypes.c_void_p()
        call = win_dxgi.com_method(_CREATE_COMMITTED, self.device, SLOT_CREATE_COMMITTED_RESOURCE)
        hr = call(self.device, ctypes.byref(heap), D3D12_HEAP_FLAG_NONE, ctypes.byref(desc),
                  D3D12_RESOURCE_STATE_COMMON, None, ctypes.byref(IID_ID3D12Resource), ctypes.byref(resource))
        if hr < 0 or not resource.value:
            raise D3D12Error(f"CreateCommittedResource of {nbytes} bytes failed ({_hex(hr)})")
        self.resources.append(resource)

    def free(self):
        """Release every resource, then the device (safe to call twice)."""
        while self.resources:
            win_dxgi.release(self.resources.pop())
        if self.device is not None:
            device, self.device = self.device, None
            win_dxgi.release(device)
