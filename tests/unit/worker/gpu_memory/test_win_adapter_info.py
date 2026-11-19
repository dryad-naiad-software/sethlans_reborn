# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``gpu_memory.win_adapter_info``.

The three D3DKMT exports are replaced with Python fakes (``_get_api`` is
patched), so no system DLL is loaded and the tests run on every OS. The fakes
write into the caller's ctypes structures the way the kernel interface would.
"""
import ctypes
from types import SimpleNamespace

from sethlans_worker_agent.gpu_memory import win_adapter_info as wai


class _FakeD3dkmt:
    """Fake ``D3DKMTOpenAdapterFromLuid`` / ``QueryAdapterInfo`` / ``CloseAdapter``."""

    def __init__(self, address=(1, 0, 0), open_status=0, query_status=0, handle=7):
        self.address = address
        self.open_status = open_status
        self.query_status = query_status
        self.handle = handle
        self.opened_luid = None
        self.closed = []

    def open_adapter(self, ref):
        opened = ref._obj
        self.opened_luid = (opened.AdapterLuid.HighPart, opened.AdapterLuid.LowPart)
        if self.open_status == 0:
            opened.hAdapter = self.handle
        return self.open_status

    def query_info(self, ref):
        query = ref._obj
        assert query.hAdapter == self.handle
        assert query.Type == wai.KMTQAITYPE_ADAPTERADDRESS
        assert query.PrivateDriverDataSize == ctypes.sizeof(wai.D3DKMT_ADAPTERADDRESS)
        if self.query_status == 0:
            out = ctypes.cast(query.pPrivateDriverData, ctypes.POINTER(wai.D3DKMT_ADAPTERADDRESS)).contents
            out.BusNumber, out.DeviceNumber, out.FunctionNumber = self.address
        return self.query_status

    def close_adapter(self, ref):
        self.closed.append(ref._obj.hAdapter)
        return 0

    def api(self):
        return SimpleNamespace(open_adapter=self.open_adapter, query_info=self.query_info,
                               close_adapter=self.close_adapter)


def _use(mocker, fake):
    mocker.patch.object(wai, '_get_api', return_value=fake.api())


class TestPciAddress:

    def test_reads_and_normalises_the_address(self, mocker):
        fake = _FakeD3dkmt(address=(0x23, 0, 0))
        _use(mocker, fake)

        assert wai.pci_address((0, 0x1234)) == '0000:23:00.0'
        assert fake.opened_luid == (0, 0x1234)
        assert fake.closed == [7]

    def test_negative_low_part_is_masked_to_unsigned(self, mocker):
        fake = _FakeD3dkmt()
        _use(mocker, fake)

        wai.pci_address((-1, -1))

        assert fake.opened_luid == (-1, 0xFFFFFFFF)

    def test_all_ones_address_of_the_software_adapter_is_none(self, mocker):
        fake = _FakeD3dkmt(address=(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF))
        _use(mocker, fake)

        assert wai.pci_address((0, 1)) is None
        assert fake.closed == [7]

    def test_failing_query_is_none_and_the_handle_is_closed(self, mocker):
        fake = _FakeD3dkmt(query_status=-1073741823)   # STATUS_UNSUCCESSFUL
        _use(mocker, fake)

        assert wai.pci_address((0, 1)) is None
        assert fake.closed == [7]

    def test_failing_open_is_none_and_nothing_is_closed(self, mocker):
        fake = _FakeD3dkmt(open_status=-1073741811)    # STATUS_INVALID_PARAMETER
        _use(mocker, fake)

        assert wai.pci_address((0, 1)) is None
        assert fake.closed == []

    def test_no_api_is_none(self, mocker):
        mocker.patch.object(wai, '_get_api', return_value=None)

        assert wai.pci_address((0, 1)) is None

    def test_exception_from_the_binding_is_none(self, mocker):
        def broken(ref):
            raise OSError('access violation')
        mocker.patch.object(wai, '_get_api', return_value=SimpleNamespace(open_adapter=broken))

        assert wai.pci_address((0, 1)) is None

    def test_malformed_luid_is_none(self, mocker):
        _use(mocker, _FakeD3dkmt())

        assert wai.pci_address(None) is None


class TestAvailable:

    def test_true_when_exports_load(self, mocker):
        mocker.patch.object(wai, '_get_api', return_value=object())

        assert wai.available() is True

    def test_false_when_exports_do_not_load(self, mocker):
        mocker.patch.object(wai, '_get_api', return_value=None)

        assert wai.available() is False

    def test_get_api_off_windows_is_none_without_loading(self, mocker):
        mocker.patch.object(wai, 'sys', SimpleNamespace(platform='linux'))
        api_class = mocker.patch.object(wai, '_Api')

        assert wai._get_api() is None
        api_class.assert_not_called()

    def test_get_api_load_failure_is_none(self, mocker):
        mocker.patch.object(wai, 'sys', SimpleNamespace(platform='win32'))
        mocker.patch.object(wai, '_api', None)
        mocker.patch.object(wai, '_Api', side_effect=OSError('gdi32.dll'))

        assert wai._get_api() is None
