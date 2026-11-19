# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the pure parts of ``gpu_memory.win_dxgi``.

No DXGI call is made: the vtable lookup (``_method``), the adapter read
(``_read_desc``) and ``_release`` are replaced with fakes, so these tests run
on every OS. They cover the order and index rules of FR-CI-2: the index is the
raw loop index counting every adapter, ``EnumAdapterByGpuPreference`` with
``HIGH_PERFORMANCE`` when ``IDXGIFactory6`` exists, ``EnumAdapters1`` only
when it does not.
"""
import ctypes
from types import SimpleNamespace

from sethlans_worker_agent.gpu_memory import win_dxgi

NOT_FOUND_HR = win_dxgi.DXGI_ERROR_NOT_FOUND - (1 << 32)   # as a signed HRESULT (c_long)
E_FAIL = -2147467259


def _desc(index, name):
    return win_dxgi.AdapterDesc(index, name, 0x1002, 0x744C, 1 << 30, (0, 100 + index), 0)


class _FakeWalk:
    """Scripted ``_next_adapter`` / ``_read_desc`` / ``_release``."""

    def __init__(self, mocker, results, descs=None):
        self.results = list(results)
        self.descs = descs
        self.indexes = []
        self.released = 0
        mocker.patch.object(win_dxgi, '_next_adapter', side_effect=self.next_adapter)
        mocker.patch.object(win_dxgi, '_read_desc', side_effect=self.read_desc)
        mocker.patch.object(win_dxgi, '_release', side_effect=self.release)

    def next_adapter(self, api, factory, factory6, index, adapter):
        self.indexes.append(index)
        return self.results[index] if index < len(self.results) else NOT_FOUND_HR

    def read_desc(self, api, adapter, index):
        if self.descs is not None:
            return self.descs[index]
        return _desc(index, f'adapter {index}')

    def release(self, api, obj):
        self.released += 1


class TestWalk:

    def test_index_is_the_raw_loop_index_counting_every_adapter(self, mocker):
        fake = _FakeWalk(mocker, [0, 0, 0])

        found = win_dxgi._walk(object(), 'factory', 'factory6')

        assert [desc.index for desc in found] == [0, 1, 2]
        assert [desc.name for desc in found] == ['adapter 0', 'adapter 1', 'adapter 2']
        assert fake.indexes == [0, 1, 2, 3]   # stops at the first NOT_FOUND
        assert fake.released == 3              # every obtained adapter is released

    def test_not_found_as_unsigned_value_also_ends_the_list(self, mocker):
        _FakeWalk(mocker, [0, win_dxgi.DXGI_ERROR_NOT_FOUND])

        found = win_dxgi._walk(object(), 'factory', None)

        assert [desc.index for desc in found] == [0]

    def test_no_adapter_gives_empty_tuple(self, mocker):
        fake = _FakeWalk(mocker, [])

        assert win_dxgi._walk(object(), 'factory', None) == ()
        assert fake.released == 0

    def test_any_other_failure_makes_the_whole_list_none(self, mocker):
        fake = _FakeWalk(mocker, [0, E_FAIL, 0])

        assert win_dxgi._walk(object(), 'factory', None) is None
        assert fake.released == 2   # the failing slot is released too

    def test_missing_description_makes_the_whole_list_none(self, mocker):
        fake = _FakeWalk(mocker, [0, 0], descs=[_desc(0, 'a'), None])

        assert win_dxgi._walk(object(), 'factory', None) is None
        assert fake.released == 2

    def test_list_is_capped_at_max_adapters(self, mocker):
        fake = _FakeWalk(mocker, [0] * (win_dxgi.MAX_ADAPTERS + 5))

        found = win_dxgi._walk(object(), 'factory', None)

        assert len(found) == win_dxgi.MAX_ADAPTERS
        assert fake.indexes[-1] == win_dxgi.MAX_ADAPTERS - 1


class TestNextAdapter:

    @staticmethod
    def _record_method(mocker):
        calls = []

        def method(prototype, obj, slot):
            def call(*args):
                calls.append((prototype, obj, slot, args))
                return 0
            return call

        mocker.patch.object(win_dxgi, '_method', side_effect=method)
        return calls

    def test_factory6_uses_enum_by_gpu_preference_high_performance(self, mocker):
        calls = self._record_method(mocker)
        api = SimpleNamespace(enum_by_preference='PREF', enum_adapters1='ENUM1')
        adapter = ctypes.c_void_p()

        assert win_dxgi._next_adapter(api, 'factory', 'factory6', 4, adapter) == 0

        prototype, obj, slot, args = calls[0]
        assert (prototype, obj, slot) == ('PREF', 'factory6', win_dxgi.SLOT_ENUM_BY_GPU_PREFERENCE)
        assert args[0] == 'factory6'
        assert args[1] == 4
        assert args[2] == win_dxgi.DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE == 2
        assert args[3]._obj is win_dxgi.IID_IDXGIAdapter1
        assert args[4]._obj is adapter

    def test_without_factory6_falls_back_to_enum_adapters1(self, mocker):
        calls = self._record_method(mocker)
        api = SimpleNamespace(enum_by_preference='PREF', enum_adapters1='ENUM1')
        adapter = ctypes.c_void_p()

        win_dxgi._next_adapter(api, 'factory', None, 2, adapter)

        prototype, obj, slot, args = calls[0]
        assert (prototype, obj, slot) == ('ENUM1', 'factory', win_dxgi.SLOT_ENUM_ADAPTERS1)
        assert args[:2] == ('factory', 2)
        assert args[2]._obj is adapter


class TestEnumerate:

    def _api(self, factory_hr=0):
        def create_factory1(iid_ref, out_ref):
            assert iid_ref._obj is win_dxgi.IID_IDXGIFactory1
            if factory_hr == 0:
                out_ref._obj.value = 0x1000
            return factory_hr
        return SimpleNamespace(create_factory1=create_factory1, query_interface='QI')

    def _patch(self, mocker, qi_hr):
        def method(prototype, obj, slot):
            assert (prototype, slot) == ('QI', win_dxgi.SLOT_QUERY_INTERFACE)

            def query(factory, iid_ref, out_ref):
                assert iid_ref._obj is win_dxgi.IID_IDXGIFactory6
                if qi_hr == 0:
                    out_ref._obj.value = 0x2000
                return qi_hr
            return query

        mocker.patch.object(win_dxgi, '_method', side_effect=method)
        walk = mocker.patch.object(win_dxgi, '_walk', return_value=('listed',))
        released = []
        mocker.patch.object(win_dxgi, '_release', side_effect=lambda api, obj: released.append(obj.value))
        return walk, released

    def test_factory6_present_walks_by_gpu_preference(self, mocker):
        walk, released = self._patch(mocker, qi_hr=0)

        assert win_dxgi._enumerate(self._api()) == ('listed',)

        factory6 = walk.call_args.args[2]
        assert factory6.value == 0x2000
        assert released == [0x2000, 0x1000]   # factory6 then factory

    def test_factory6_missing_walks_with_enum_adapters1(self, mocker):
        walk, released = self._patch(mocker, qi_hr=E_FAIL)

        assert win_dxgi._enumerate(self._api()) == ('listed',)

        assert walk.call_args.args[2] is None
        assert 0x1000 in released

    def test_factory_creation_failure_is_none(self, mocker):
        walk, released = self._patch(mocker, qi_hr=0)

        assert win_dxgi._enumerate(self._api(factory_hr=E_FAIL)) is None
        walk.assert_not_called()
        assert released == []


class TestEnumerateAdapters:

    def test_off_windows_is_none_without_loading_anything(self, mocker):
        mocker.patch.object(win_dxgi, 'sys', SimpleNamespace(platform='linux'))
        get_api = mocker.patch.object(win_dxgi, '_get_api')

        assert win_dxgi.enumerate_adapters() is None
        get_api.assert_not_called()

    def test_missing_dll_is_none_not_an_exception(self, mocker):
        mocker.patch.object(win_dxgi, 'sys', SimpleNamespace(platform='win32'))
        mocker.patch.object(win_dxgi, '_get_api', side_effect=OSError('dxgi.dll not found'))

        assert win_dxgi.enumerate_adapters() is None

    def test_returns_the_enumerated_list(self, mocker):
        mocker.patch.object(win_dxgi, 'sys', SimpleNamespace(platform='win32'))
        mocker.patch.object(win_dxgi, '_get_api', return_value='api')
        listed = (_desc(0, 'AMD Radeon RX 7900 XT'),)
        enumerate_ = mocker.patch.object(win_dxgi, '_enumerate', return_value=listed)

        assert win_dxgi.enumerate_adapters() == listed
        enumerate_.assert_called_once_with('api')
