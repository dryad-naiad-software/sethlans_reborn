# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``idle_detection.win32_api`` (issue #234).

Regression: ``session_win32`` called ``CreateWindowExW`` with no
``argtypes``, so ctypes converted the 64-bit ``hInstance`` as a C int and
raised ``OverflowError: int too long to convert``. Every Win32 function
the idle-detection package calls must now carry explicit, pointer-sized
declarations, made once on private ``WinDLL`` instances.
"""
import ast
import ctypes
import ctypes.wintypes
import inspect
import sys
from pathlib import Path

import pytest

PKG = 'sethlans_worker_agent.idle_detection'
PKG_DIR = Path(__file__).resolve().parents[3] / 'worker' / 'sethlans_worker_agent' / 'idle_detection'

windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='Windows-only Win32 bindings')

# Every Win32 function the idle-detection package calls.
EXPECTED_FUNCTIONS = {
    'kernel32': {'GetModuleHandleW', 'GetTickCount64'},
    'user32': {
        'RegisterClassExW', 'UnregisterClassW', 'CreateWindowExW', 'DestroyWindow',
        'IsWindow', 'DefWindowProcW', 'PostMessageW', 'PostQuitMessage', 'GetMessageW',
        'TranslateMessage', 'DispatchMessageW', 'GetLastInputInfo',
    },
    'wtsapi32': {'WTSRegisterSessionNotification', 'WTSUnRegisterSessionNotification'},
}

PTR = ctypes.sizeof(ctypes.c_void_p)


@pytest.fixture()
def api():
    from sethlans_worker_agent.idle_detection import win32_api
    return win32_api


def _called_win32_names(module_file: str) -> set[str]:
    """Names of ``api.<dll>.<Function>`` attribute calls in a module."""
    tree = ast.parse((PKG_DIR / module_file).read_text(encoding='utf-8'))
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in EXPECTED_FUNCTIONS):
            names.add(node.func.attr)
    return names


class TestCallersUseTypedBindings:
    """Cross-platform source checks on the calling modules."""

    @pytest.mark.parametrize('module_file', ['session_win32.py', 'input_monitor.py'])
    def test_no_shared_windll_access(self, module_file):
        """Callers must not touch (or mutate) the process-wide ``ctypes.windll``."""
        src = (PKG_DIR / module_file).read_text(encoding='utf-8')
        assert 'windll' not in src

    def test_every_called_function_is_declared(self):
        """Each user32/kernel32/wtsapi32 call in the package has a declaration."""
        declared = set().union(*EXPECTED_FUNCTIONS.values())
        called = _called_win32_names('session_win32.py') | _called_win32_names('input_monitor.py')
        assert called, 'AST scan found no Win32 calls; the scan is broken'
        assert called <= declared, f'undeclared Win32 calls: {called - declared}'

    @pytest.mark.skipif(sys.platform == 'win32', reason='non-Windows import guard')
    def test_import_fails_cleanly_off_windows(self):
        with pytest.raises(ImportError):
            __import__(f'{PKG}.win32_api')


@windows_only
class TestDeclarations:
    """Windows: the declarations themselves."""

    def test_signature_table_covers_every_function(self, api):
        table = {}
        for dll, name, _args, _res in api.SIGNATURES:
            table.setdefault(dll._name, set()).add(name)
        assert table == EXPECTED_FUNCTIONS

    def test_every_function_has_argtypes_and_restype(self, api):
        for dll, name, argtypes, restype in api.SIGNATURES:
            fn = getattr(dll, name)
            assert fn.argtypes == argtypes, name
            assert fn.restype is restype, name
            assert fn.argtypes is not None, name

    def test_private_dlls_not_shared_windll(self, api):
        assert api.user32 is not ctypes.windll.user32
        assert api.kernel32 is not ctypes.windll.kernel32
        assert api.wtsapi32 is not ctypes.windll.wtsapi32
        assert api.user32.CreateWindowExW is not ctypes.windll.user32.CreateWindowExW

    def test_message_params_are_pointer_sized(self, api):
        for t in (api.LRESULT, api.WPARAM, api.LPARAM):
            assert ctypes.sizeof(t) == PTR
        assert api.LRESULT(-1).value == -1  # LONG_PTR is signed
        assert api.LPARAM(-1).value == -1
        assert api.WPARAM(-1).value == (1 << (8 * PTR)) - 1  # UINT_PTR is unsigned

    def test_create_window_handles_are_pointer_sized(self, api):
        fn = api.user32.CreateWindowExW
        assert len(fn.argtypes) == 12
        for index in (8, 9, 10, 11):  # hWndParent, hMenu, hInstance, lpParam
            assert ctypes.sizeof(fn.argtypes[index]) == PTR
        assert ctypes.sizeof(fn.restype) == PTR

    def test_create_window_accepts_64bit_hinstance(self, api):
        """The exact conversion that overflowed in #234 now succeeds."""
        big = 0x7FF6_0000_0000 if PTR == 8 else 0x7FFF_0000
        converted = api.user32.CreateWindowExW.argtypes[10].from_param(big)
        assert converted is not None

    def test_get_module_handle_returns_full_handle(self, api):
        assert api.kernel32.GetModuleHandleW.restype is ctypes.wintypes.HMODULE
        handle = api.kernel32.GetModuleHandleW(None)
        assert isinstance(handle, int) and handle > 0

    def test_wndproc_signature(self, api):
        assert api.WNDPROC._restype_ is api.LRESULT
        assert api.WNDPROC._argtypes_ == (
            ctypes.wintypes.HWND, ctypes.wintypes.UINT, api.WPARAM, api.LPARAM,
        )
        assert api.user32.DefWindowProcW.restype is api.LRESULT
        assert api.user32.DefWindowProcW.argtypes == list(api.WNDPROC._argtypes_)

    def test_struct_layouts_match_win32(self, api):
        assert ctypes.sizeof(api.WNDCLASSEXW) == (80 if PTR == 8 else 48)
        assert ctypes.sizeof(api.LASTINPUTINFO) == 8

    def test_session_module_uses_api_wndproc(self, api):
        from sethlans_worker_agent.idle_detection import session_win32
        callback = session_win32._make_wnd_proc(api)
        assert isinstance(callback, api.WNDPROC)
        assert 'WINFUNCTYPE' not in inspect.getsource(session_win32)
