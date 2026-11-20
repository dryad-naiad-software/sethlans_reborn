# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Windows performance counters through ``pdh.dll`` (Windows only).

``PdhQuery(paths)`` opens one query with an English counter path per entry
(``PdhOpenQueryW`` and ``PdhAddEnglishCounterW``) and keeps it open until
``close()``. ``collect()`` does one ``PdhCollectQueryData`` and one
``PdhGetFormattedCounterArrayW`` per path (``PDH_FMT_LARGE``) and returns
``{path: [(instance, value, cstatus), ...] or None}``: ``None`` for a path
whose array could not be read, and ``None`` instead of the dict when the
collect itself failed. ``PDH_NO_DATA`` and ``PDH_CSTATUS_NO_INSTANCE`` give
an empty list. It never raises; the constructor raises ``PdhError`` when the
query cannot be opened. ``PdhQuery`` is the seam the unit tests fake.

Rules: a private ``WinDLL`` loaded from System32 on first use; every
function declared once with ``argtypes``/``restype``; nothing runs at import,
so the module imports on every platform. The array buffer is sized from the
API's answer (at most ``MAX_BUFFER_BYTES``, at most ``MAX_SIZED_CALLS`` sized
calls), and instance names are read only from inside that buffer.
"""
import ctypes
import logging
import sys
import threading
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
PDH_FMT_LARGE = 0x00000400
PDH_CSTATUS_VALID_DATA = 0x00000000
PDH_CSTATUS_NEW_DATA = 0x00000001
PDH_CSTATUS_NO_INSTANCE = 0x800007D1
PDH_MORE_DATA = 0x800007D2
PDH_NO_DATA = 0x800007D5
ERROR_SUCCESS = 0
MAX_BUFFER_BYTES = 16 * 1024 * 1024
MAX_SIZED_CALLS = 3
MAX_NAME_CHARS = 512

# (instance name, value, CStatus)
Item = Tuple[str, int, int]


class _Value(ctypes.Union):
    _fields_ = [('longValue', ctypes.c_int32), ('doubleValue', ctypes.c_double), ('largeValue', ctypes.c_int64),
                ('AnsiStringValue', ctypes.c_void_p), ('WideStringValue', ctypes.c_void_p)]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _anonymous_ = ('value',)
    _fields_ = [('CStatus', ctypes.c_uint32), ('value', _Value)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [('szName', ctypes.c_void_p), ('FmtValue', PDH_FMT_COUNTERVALUE)]


class PdhError(OSError):
    """The query could not be opened (no DLL, no export, a counter path refused)."""


_api_lock = threading.Lock()
_api = None


class _Api:
    """The five PDH functions, bound once."""

    def __init__(self):
        from ctypes import wintypes
        dll = ctypes.WinDLL('pdh.dll', winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
        handle_out = ctypes.POINTER(ctypes.c_void_p)
        self.open_query = self._bind(dll.PdhOpenQueryW, [ctypes.c_wchar_p, ctypes.c_size_t, handle_out], wintypes)
        self.add_counter = self._bind(dll.PdhAddEnglishCounterW,
                                      [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_size_t, handle_out], wintypes)
        self.collect = self._bind(dll.PdhCollectQueryData, [ctypes.c_void_p], wintypes)
        self.get_array = self._bind(dll.PdhGetFormattedCounterArrayW,
                                    [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes)
        self.close_query = self._bind(dll.PdhCloseQuery, [ctypes.c_void_p], wintypes)

    @staticmethod
    def _bind(function, argtypes, wintypes):
        function.argtypes = argtypes
        function.restype = wintypes.DWORD  # PDH_STATUS
        return function


def _get_api() -> _Api:
    global _api
    if sys.platform != 'win32':
        raise PdhError("PDH exists only on Windows")
    with _api_lock:
        if _api is None:
            try:
                _api = _Api()
            except (OSError, AttributeError) as exc:
                raise PdhError(f"pdh.dll unavailable: {exc}") from exc
        return _api


def decode_items(buffer, size: int, count: int) -> Optional[List[Item]]:
    """The ``count`` items at the start of ``buffer`` (``size`` bytes); ``None`` when they do not fit in it.

    Each name is read as UTF-16 up to its NUL, only from inside the buffer; a name pointing outside is ''.
    """
    item_size = ctypes.sizeof(PDH_FMT_COUNTERVALUE_ITEM_W)
    if count < 0 or count * item_size > size or size > ctypes.sizeof(buffer):
        return None
    start = ctypes.addressof(buffer)
    end = start + size
    items = (PDH_FMT_COUNTERVALUE_ITEM_W * count).from_buffer(buffer) if count else ()
    out = []
    for item in items:
        out.append((_name_at(item.szName, start, end), int(item.FmtValue.largeValue), int(item.FmtValue.CStatus)))
    return out


def _name_at(pointer: Optional[int], start: int, end: int) -> str:
    if not pointer or not start <= pointer < end:
        return ''
    nbytes = min(end - pointer, 2 * MAX_NAME_CHARS) & ~1
    text = ctypes.string_at(pointer, nbytes).decode('utf-16-le', errors='replace')
    return text.split('\x00', 1)[0]


ArrayCall = Callable[[object, ctypes.c_uint32, ctypes.c_uint32, Optional[ctypes.Array]], int]


def read_array(call: ArrayCall, counter) -> Optional[List[Item]]:
    """The formatted items of one wildcard counter, or ``None``.

    ``call(counter, size, count, buffer)`` is ``PdhGetFormattedCounterArrayW`` with ``size``/``count`` in-out
    ``c_uint32`` and ``buffer`` a byte array or ``None``. First a zero-size call; on ``PDH_MORE_DATA`` a buffer
    of the size it asks for, repeated at most ``MAX_SIZED_CALLS`` times. No instance is ``[]``.
    """
    size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
    buffer = None
    for _attempt in range(MAX_SIZED_CALLS + 1):
        status = call(counter, size, count, buffer) & 0xFFFFFFFF
        if status in (PDH_NO_DATA, PDH_CSTATUS_NO_INSTANCE):
            return []
        if status == ERROR_SUCCESS:
            return [] if buffer is None else decode_items(buffer, size.value, count.value)
        if status != PDH_MORE_DATA or not 0 < size.value <= MAX_BUFFER_BYTES:
            logger.debug("PdhGetFormattedCounterArrayW failed (0x%08X, %d bytes).", status, size.value)
            return None
        buffer = (ctypes.c_byte * size.value)()
    logger.debug("PdhGetFormattedCounterArrayW kept asking for more room.")
    return None


class PdhQuery:
    """One open PDH query with one counter per path. Used under the GPU memory reader's lock."""

    def __init__(self, paths: Sequence[str], api: Optional[_Api] = None):
        self._api = api if api is not None else _get_api()
        self._lock = threading.Lock()  # leaf lock: the handle is used by one call at a time
        self._handle = ctypes.c_void_p()
        self._counters: Dict[str, ctypes.c_void_p] = {}
        status = self._api.open_query(None, 0, ctypes.byref(self._handle))
        if status != ERROR_SUCCESS:
            raise PdhError(f"PdhOpenQueryW failed (0x{status & 0xFFFFFFFF:08X})")
        try:
            for path in paths:
                counter = ctypes.c_void_p()
                status = self._api.add_counter(self._handle, path, 0, ctypes.byref(counter))
                if status != ERROR_SUCCESS:
                    raise PdhError(f"PdhAddEnglishCounterW failed for {path} (0x{status & 0xFFFFFFFF:08X})")
                self._counters[path] = counter
        except BaseException:
            self.close()
            raise

    def collect(self) -> Optional[Dict[str, Optional[List[Item]]]]:
        """One collect and every counter's array; ``None`` when the collect failed. Never raises."""
        try:
            with self._lock:
                if not self._handle.value:
                    return None
                status = self._api.collect(self._handle)
                if status != ERROR_SUCCESS:
                    logger.debug("PdhCollectQueryData failed (0x%08X).", status & 0xFFFFFFFF)
                    return None
                return {path: read_array(self._array_call, counter) for path, counter in self._counters.items()}
        except (OSError, TypeError, ValueError, ctypes.ArgumentError) as exc:
            logger.debug("PDH collect failed: %s", exc)
            return None

    def _array_call(self, counter, size, count, buffer) -> int:
        return self._api.get_array(counter, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buffer)

    def close(self) -> None:
        """Close the query (once). Never raises."""
        with self._lock:
            handle, self._handle = self._handle, ctypes.c_void_p()
            self._counters = {}
        if handle.value:
            try:
                self._api.close_query(handle)
            except (OSError, TypeError, ValueError, ctypes.ArgumentError) as exc:
                logger.debug("PdhCloseQuery failed: %s", exc)
