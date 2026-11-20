# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``gpu_memory.win_pdh`` without ``pdh.dll`` (GPU memory reader FR-GW-1, FR-GW-9).

The array loop, the item decoding and ``PdhQuery`` run against fakes that fill real ctypes buffers, so these
tests run on every OS. Instance names are made up.
"""
import ctypes
import sys

import pytest

from sethlans_worker_agent.gpu_memory import win_pdh
from sethlans_worker_agent.gpu_memory.win_pdh import PDH_FMT_COUNTERVALUE_ITEM_W as ITEM
from tests.unit.worker.gpu_memory._win_fakes import FakeApi

ITEM_SIZE = ctypes.sizeof(ITEM)


def _needed(entries):
    return len(entries) * ITEM_SIZE + sum(len((name + '\x00').encode('utf-16-le')) for name, _v, _s in entries)


def _fill(buffer, entries):
    base, offset = ctypes.addressof(buffer), len(entries) * ITEM_SIZE
    items = (ITEM * len(entries)).from_buffer(buffer) if entries else ()
    for item, (name, value, status) in zip(items, entries):
        data = (name + '\x00').encode('utf-16-le')
        ctypes.memmove(base + offset, data, len(data))
        item.szName, item.FmtValue.CStatus, item.FmtValue.largeValue = base + offset, status, value
        offset += len(data)


class FakeArray:
    """``PdhGetFormattedCounterArrayW``: call ``k`` sees ``script[k]`` (the last repeats) as the instances."""

    def __init__(self, *script, status=None):
        self.script, self.status, self.sizes = list(script), status, []

    def __call__(self, counter, size, count, buffer):
        entries = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        self.sizes.append(None if buffer is None else ctypes.sizeof(buffer))
        if self.status is not None:
            return self.status
        needed = _needed(entries)
        if buffer is None and not entries:
            size.value, count.value = 0, 0
            return win_pdh.ERROR_SUCCESS
        if buffer is None or ctypes.sizeof(buffer) < needed:
            size.value = needed
            return win_pdh.PDH_MORE_DATA
        _fill(buffer, entries)
        size.value, count.value = needed, len(entries)
        return win_pdh.ERROR_SUCCESS


ONE = [('luid_0x00000000_0x0000AAAA_phys_0', 512 * 1024 * 1024, 0)]
TWO = ONE + [('luid_0x00000000_0x0000BBBB_phys_0', 7, 1)]
THREE = TWO + [('luid_0x00000000_0x0000CCCC_phys_0', 9, 0)]


@pytest.mark.skipif(sys.maxsize <= 2 ** 32, reason='64-bit layout')
def test_structure_sizes_on_64_bit():
    assert ctypes.sizeof(win_pdh.PDH_FMT_COUNTERVALUE) == 16
    assert ITEM_SIZE == 24


class TestReadArray:

    def test_fits_after_one_sized_call(self):
        call = FakeArray(ONE)
        assert win_pdh.read_array(call, 'c') == ONE
        assert call.sizes == [None, _needed(ONE)]

    def test_grows_twice_then_fits(self):
        call = FakeArray(ONE, TWO, THREE)
        assert win_pdh.read_array(call, 'c') == THREE
        assert call.sizes == [None, _needed(ONE), _needed(TWO), _needed(THREE)]

    def test_still_growing_after_three_sized_calls_fails(self):
        call = FakeArray(ONE, TWO, THREE, THREE + [('x', 1, 0)], THREE + [('x', 1, 0), ('y', 1, 0)])
        assert win_pdh.read_array(call, 'c') is None
        assert len(call.sizes) == 4

    def test_an_oversize_buffer_request_fails_before_allocating(self, monkeypatch):
        monkeypatch.setattr(win_pdh, 'MAX_BUFFER_BYTES', _needed(ONE) - 1)
        call = FakeArray(ONE)
        assert win_pdh.read_array(call, 'c') is None
        assert call.sizes == [None]

    def test_no_instance_at_all_is_an_empty_list(self):
        assert win_pdh.read_array(FakeArray([]), 'c') == []

    @pytest.mark.parametrize('status', [win_pdh.PDH_NO_DATA, win_pdh.PDH_CSTATUS_NO_INSTANCE])
    def test_no_data_statuses_are_an_empty_list(self, status):
        assert win_pdh.read_array(FakeArray(ONE, status=status), 'c') == []

    def test_another_status_fails(self):
        assert win_pdh.read_array(FakeArray(ONE, status=0xC0000BB8), 'c') is None


class TestDecode:

    def test_a_count_beyond_the_buffer_is_none(self):
        buffer = (ctypes.c_byte * _needed(ONE))()
        _fill(buffer, ONE)
        assert win_pdh.decode_items(buffer, _needed(ONE), _needed(ONE) // ITEM_SIZE + 1) is None
        assert win_pdh.decode_items(buffer, _needed(ONE) + 8, 1) is None

    def test_a_name_outside_the_buffer_is_empty(self):
        buffer = (ctypes.c_byte * _needed(ONE))()
        _fill(buffer, ONE)
        outside = ctypes.create_unicode_buffer('elsewhere')
        ITEM.from_buffer(buffer).szName = ctypes.addressof(outside)
        assert win_pdh.decode_items(buffer, _needed(ONE), 1) == [('', 512 * 1024 * 1024, 0)]

    def test_a_name_without_nul_stops_at_the_buffer_end(self):
        buffer = (ctypes.c_byte * (ITEM_SIZE + 6))()
        ctypes.memmove(ctypes.addressof(buffer) + ITEM_SIZE, 'abc'.encode('utf-16-le'), 6)
        ITEM.from_buffer(buffer).szName = ctypes.addressof(buffer) + ITEM_SIZE
        assert win_pdh.decode_items(buffer, ITEM_SIZE + 6, 1)[0][0] == 'abc'


class TestPdhQuery:

    def test_collect_reads_every_counter(self):
        api = FakeApi(arrays={1: FakeArray(ONE), 2: FakeArray([], status=win_pdh.PDH_NO_DATA)})
        query = win_pdh.PdhQuery(['\\A(*)\\x', '\\B(*)\\y'], api=api)
        assert query.collect() == {'\\A(*)\\x': ONE, '\\B(*)\\y': []}
        assert api.log == ['open', 'add \\A(*)\\x', 'add \\B(*)\\y', 'collect']

    def test_a_failed_collect_is_none(self):
        query = win_pdh.PdhQuery(['\\A(*)\\x'], api=FakeApi(fail='collect', arrays={1: FakeArray(ONE)}))
        assert query.collect() is None

    def test_a_failed_array_is_none_for_that_counter_only(self):
        api = FakeApi(arrays={1: FakeArray(ONE, status=0xC0000BB8), 2: FakeArray(ONE)})
        assert win_pdh.PdhQuery(['a', 'b'], api=api).collect() == {'a': None, 'b': ONE}

    def test_a_query_that_cannot_open_raises(self):
        with pytest.raises(win_pdh.PdhError):
            win_pdh.PdhQuery(['a'], api=FakeApi(fail='open'))

    def test_a_refused_counter_closes_the_query_and_raises(self):
        api = FakeApi(fail='add b')
        with pytest.raises(win_pdh.PdhError):
            win_pdh.PdhQuery(['a', 'b'], api=api)
        assert api.log[-1] == 'close'

    def test_close_once_then_collect_is_none(self):
        api = FakeApi(arrays={1: FakeArray(ONE)})
        query = win_pdh.PdhQuery(['a'], api=api)
        query.close()
        query.close()
        assert api.log.count('close') == 1 and query.collect() is None

    def test_off_windows_no_dll_is_loaded(self, monkeypatch):
        monkeypatch.setattr(win_pdh.sys, 'platform', 'linux')
        monkeypatch.setattr(win_pdh, '_api', None)
        with pytest.raises(win_pdh.PdhError):
            win_pdh.PdhQuery(['a'])
        assert win_pdh._api is None


CTYPES_ERRORS = [OSError('access violation'), ctypes.ArgumentError('bad argument'), TypeError('bad type'),
                 ValueError('bad value')]


class RaisingApi(FakeApi):
    """``FakeApi`` whose call ``name`` raises ``error`` instead of returning a status."""

    def __init__(self, name, error, **kwargs):
        super().__init__(**kwargs)
        self.raising, self.error = name, error

    def _status(self, name):
        if name == self.raising:
            self.log.append(name + ' raised')
            raise self.error
        return super()._status(name)


class TestCtypesErrorsFailClosed:
    """FR-GW-1: every ctypes failure gives "no reading", never an exception out of ``collect()`` or ``close()``."""

    @pytest.mark.parametrize('error', CTYPES_ERRORS)
    def test_a_raising_collect_is_none(self, error):
        query = win_pdh.PdhQuery(['a'], api=RaisingApi('collect', error, arrays={1: FakeArray(ONE)}))
        assert query.collect() is None

    @pytest.mark.parametrize('error', CTYPES_ERRORS)
    def test_a_raising_array_call_is_none(self, error):
        def array(counter, size, count, buffer):
            raise error

        assert win_pdh.PdhQuery(['a'], api=FakeApi(arrays={1: array})).collect() is None

    @pytest.mark.parametrize('error', CTYPES_ERRORS)
    def test_a_raising_close_is_swallowed_and_the_query_is_closed(self, error):
        api = RaisingApi('close', error)
        query = win_pdh.PdhQuery(['a'], api=api)
        query.close()
        query.close()
        assert api.log.count('close raised') == 1 and query.collect() is None

    @pytest.mark.parametrize('error', [OSError('no pdh.dll'), AttributeError('no such export')])
    def test_a_dll_that_cannot_be_bound_is_a_pdh_error_and_is_retried_later(self, monkeypatch, error):
        def broken():
            raise error

        monkeypatch.setattr(win_pdh.sys, 'platform', 'win32')
        monkeypatch.setattr(win_pdh, '_api', None)
        monkeypatch.setattr(win_pdh, '_Api', broken)
        with pytest.raises(win_pdh.PdhError, match='pdh.dll unavailable'):
            win_pdh.PdhQuery(['a'])
        assert win_pdh._api is None


class TestReadArrayEdges:

    def test_more_data_without_a_size_fails(self):
        def array(counter, size, count, buffer):
            size.value = 0
            return win_pdh.PDH_MORE_DATA

        assert win_pdh.read_array(array, 'c') is None

    def test_a_signed_status_is_read_as_its_unsigned_code(self):
        signed_no_data = win_pdh.PDH_NO_DATA - 2 ** 32
        assert win_pdh.read_array(FakeArray(ONE, status=signed_no_data), 'c') == []

    def test_success_with_a_count_beyond_the_buffer_fails(self):
        def array(counter, size, count, buffer):
            if buffer is None:
                size.value = _needed(ONE)
                return win_pdh.PDH_MORE_DATA
            _fill(buffer, ONE)
            size.value, count.value = _needed(ONE), 50
            return win_pdh.ERROR_SUCCESS

        assert win_pdh.read_array(array, 'c') is None
