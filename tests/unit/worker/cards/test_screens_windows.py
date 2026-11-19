# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.screens_windows`` (FR-CS-1, AC-36) with fake DXGI outputs and a fake ``GetMonitorInfoW``.

No DLL is loaded: ``screens_windows._get_api`` gives a fake API whose
``GetMonitorInfoW`` and ``ProcessIdToSessionId`` fill the real ctypes
structures; ``win_dxgi.com_method`` dispatches ``EnumOutputs`` and
``IDXGIOutput::GetDesc`` to scripted outputs; ``win_dxgi.enumerate_adapters``
walks scripted adapters and calls the real ``visit``; ``win_adapter_info``
gives each adapter's PCI address. So the output loop, the primary-monitor
check, the key join and the failure rules run on every OS.
"""
import ctypes
import logging
import sys
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.cards import screens, screens_windows
from sethlans_worker_agent.cards.inventory import Card, Inventory
from sethlans_worker_agent.gpu_memory import win_adapter_info, win_dxgi
from sethlans_worker_agent.gpu_memory.win_dxgi import AdapterDesc

REAL_PLATFORM_MARKS = screens._platform_marks  # taken at import, before the root conftest replaces it
NOT_FOUND = win_dxgi.DXGI_ERROR_NOT_FOUND - (1 << 32)
E_FAIL = -2147467259
PRIMARY, OTHER = 0x100, 0x200              # monitor handles
KEY_A, KEY_B = 'pci:0000:01:00.0', 'pci:0000:79:00.0'


def output(attached=True, monitor=PRIMARY, desc_hr=0):
    return {'attached': attached, 'monitor': monitor, 'desc_hr': desc_hr}


class _Machine:
    """Scripted adapters ``(address, outputs or enum failure hr)`` and monitors."""

    def __init__(self, mocker):
        self.adapters = []
        self.primary = {PRIMARY: True, OTHER: False}   # monitor -> primary flag; missing: GetMonitorInfoW fails
        self.session = 1
        self.session_ok = True
        self.released = []
        self.handles = {}
        self.api = SimpleNamespace(enum_outputs='ENUM', output_desc='DESC', get_monitor_info=self.monitor_info,
                                   current_process_id=lambda: 4242, session_of=self.session_of)
        mocker.patch.object(screens_windows, '_get_api', return_value=self.api)
        mocker.patch.object(win_dxgi, 'com_method', side_effect=self.com_method)
        mocker.patch.object(win_dxgi, 'release', side_effect=lambda obj: self.released.append(obj.value))
        mocker.patch.object(win_dxgi, 'enumerate_adapters', side_effect=self.enumerate_adapters)
        mocker.patch.object(win_adapter_info, 'available', return_value=True)
        mocker.patch.object(win_adapter_info, 'pci_address', side_effect=lambda luid: self.addresses[luid])
        self.addresses = {}

    def adapter(self, address, outputs=(), enum_hr=None, flags=0):
        index = len(self.adapters)
        desc = AdapterDesc(index, f'adapter {index}', 0x10DE, 0x2B85, 1 << 30, (0, 500 + index), flags)
        self.addresses[desc.luid] = address
        self.adapters.append((desc, list(outputs), enum_hr))

    def enumerate_adapters(self, visit=None):
        return tuple((desc, visit(ctypes.c_void_p(0x1000 + desc.index))) for desc, _o, _h in self.adapters)

    def com_method(self, prototype, obj, slot):
        assert slot == 7
        if prototype == 'ENUM':
            return self.enum_outputs
        assert prototype == 'DESC'
        return self.get_desc

    def enum_outputs(self, adapter, index, out_ref):
        _desc, outputs, enum_hr = self.adapters[adapter.value - 0x1000]
        if enum_hr is not None and index == len(outputs):
            return enum_hr
        if index >= len(outputs):
            return NOT_FOUND
        handle = 0x5000 + 16 * (adapter.value - 0x1000) + index
        self.handles[handle] = outputs[index]
        out_ref._obj.value = handle
        return 0

    def get_desc(self, out, desc_ref):
        found = self.handles[out.value]
        desc_ref._obj.AttachedToDesktop = 1 if found['attached'] else 0
        desc_ref._obj.Monitor = found['monitor']
        return found['desc_hr']

    def monitor_info(self, monitor, info_ref):
        info = info_ref._obj
        assert info.cbSize == ctypes.sizeof(screens_windows.MONITORINFO)
        if monitor not in self.primary:
            return 0
        info.dwFlags = screens_windows.MONITORINFOF_PRIMARY if self.primary[monitor] else 0
        return 1

    def session_of(self, pid, session_ref):
        assert pid == 4242
        session_ref._obj.value = self.session
        return 1 if self.session_ok else 0


@pytest.fixture
def machine(mocker):
    return _Machine(mocker)


def _inventory(*keys):
    return Inventory(cards=tuple(Card(key=key, name=key) for key in keys))


class TestAc36:

    def test_attached_primary_output_is_screen_and_main_screen(self, machine):
        machine.adapter('0000:01:00.0', [output(monitor=PRIMARY)])

        assert screens_windows.read() == {KEY_A: (True, True)}

    def test_adapter_without_outputs_drives_no_screen(self, machine):
        machine.adapter('0000:79:00.0', [])

        assert screens_windows.read() == {KEY_B: (False, False)}

    def test_failed_read_is_unknown_for_every_card(self, mocker, machine):
        machine.adapter('0000:01:00.0', [output()])
        machine.adapter('0000:79:00.0', [], enum_hr=E_FAIL)
        mocker.patch.object(screens, '_platform_marks', REAL_PLATFORM_MARKS)

        assert screens_windows.read() is None
        assert screens.read(_inventory(KEY_A, KEY_B), 'win32') == {KEY_A: (None, None), KEY_B: (None, None)}


class TestOutputs:

    def test_attached_output_on_another_monitor_is_a_screen_not_the_main_one(self, machine):
        machine.adapter('0000:01:00.0', [output(monitor=OTHER)])

        assert screens_windows.read() == {KEY_A: (True, False)}

    def test_output_not_attached_to_the_desktop_is_no_screen(self, machine):
        machine.adapter('0000:01:00.0', [output(attached=False, monitor=None)])

        assert screens_windows.read() == {KEY_A: (False, False)}

    def test_primary_on_a_later_output_counts(self, machine):
        machine.adapter('0000:01:00.0', [output(monitor=OTHER), output(attached=False), output(monitor=PRIMARY)])

        assert screens_windows.read() == {KEY_A: (True, True)}

    def test_two_cards_main_screen_on_the_second(self, machine):
        machine.adapter('0000:01:00.0', [output(monitor=OTHER)])
        machine.adapter('0000:79:00.0', [output(monitor=PRIMARY)])

        assert screens_windows.read() == {KEY_A: (True, False), KEY_B: (True, True)}

    def test_every_output_is_released(self, machine):
        machine.adapter('0000:01:00.0', [output(), output(attached=False)])

        screens_windows.read()

        assert sorted(machine.released) == [0x5000, 0x5001]

    def test_at_most_16_outputs_are_read(self, machine):
        machine.adapter('0000:01:00.0', [output(attached=False)] * 20)

        assert screens_windows.read() == {KEY_A: (False, False)}
        assert len(machine.released) == screens_windows.MAX_OUTPUTS

    @pytest.mark.parametrize('broken', ['get_desc', 'monitor_info', 'no_monitor'])
    def test_any_output_failure_is_unknown_for_every_card(self, machine, broken):
        bad = {'get_desc': output(desc_hr=E_FAIL), 'monitor_info': output(monitor=0x999),
               'no_monitor': output(monitor=None)}[broken]
        machine.adapter('0000:01:00.0', [output()])
        machine.adapter('0000:79:00.0', [bad])

        assert screens_windows.read() is None
        assert 0x5010 in machine.released   # the failing output is released too


class TestAdapters:

    def test_software_and_unaddressed_adapters_are_ignored_even_when_they_fail(self, machine):
        machine.adapter(None, [], enum_hr=E_FAIL)                                  # virtual display adapter
        machine.adapter(None, [], enum_hr=E_FAIL, flags=win_dxgi.DXGI_ADAPTER_FLAG_SOFTWARE)
        machine.adapter('0000:01:00.0', [output()])

        assert screens_windows.read() == {KEY_A: (True, True)}

    def test_software_adapter_is_not_asked_for_an_address(self, machine):
        machine.adapter(None, [], flags=win_dxgi.DXGI_ADAPTER_FLAG_SOFTWARE)

        assert screens_windows.read() == {}
        win_adapter_info.pci_address.assert_not_called()

    def test_two_adapters_with_one_key_are_unknown(self, machine):
        machine.adapter('0000:01:00.0', [output()])
        machine.adapter('0000:01:00.0', [])

        assert screens_windows.read() == {KEY_A: (None, None)}

    def test_no_dxgi_list_is_none(self, mocker, machine):
        mocker.patch.object(win_dxgi, 'enumerate_adapters', return_value=None)

        assert screens_windows.read() is None

    def test_d3dkmt_unavailable_is_none(self, machine):
        machine.adapter('0000:01:00.0', [output()])
        win_adapter_info.available.return_value = False

        assert screens_windows.read() is None


class TestSession:
    """V13: no interactive desktop, so an empty output list would read as 'no screen'."""

    def test_session_0_is_unknown_and_logged_once(self, machine, caplog):
        machine.adapter('0000:01:00.0', [])
        machine.session = 0
        caplog.set_level(logging.DEBUG, logger='sethlans_worker_agent.cards.screens_windows')

        assert screens_windows.read() is None
        assert screens_windows.read() is None
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert infos == [screens_windows.SESSION_TEXT]
        win_dxgi.enumerate_adapters.assert_not_called()

    def test_unreadable_session_is_unknown(self, machine):
        machine.adapter('0000:01:00.0', [])
        machine.session_ok = False

        assert screens_windows.read() is None


@pytest.mark.skipif(sys.platform != 'win32',
                    reason='ctypes.wintypes follows the platform C types off Windows, so only the Windows layout is real')
class TestStructures:

    def test_output_desc_layout_matches_dxgi(self):
        """WCHAR[32], RECT, BOOL, DXGI_MODE_ROTATION, then the HMONITOR at byte 88."""
        assert screens_windows.DXGI_OUTPUT_DESC.Monitor.offset == 88
        assert ctypes.sizeof(screens_windows.DXGI_OUTPUT_DESC) == 88 + ctypes.sizeof(ctypes.c_void_p)

    def test_monitorinfo_is_40_bytes(self):
        assert ctypes.sizeof(screens_windows.MONITORINFO) == 40
