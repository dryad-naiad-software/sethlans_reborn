# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cross-spec Windows-resources regression tests.

Locks in the fixes for:

* Issue #96 — all four PyInstaller specs must embed ``sethlans.ico``
  via an ``icon=`` kwarg on their ``EXE(...)`` block, guarded on
  ``ICON_WIN.exists()`` so non-Windows builds don't crash.

* Issue #109 — all four specs must wire a Windows VERSIONINFO
  resource into ``EXE(version=...)`` via the
  ``packaging/pyinstaller/version_info.make_version_info`` helper,
  so Task Manager / File Properties / SmartScreen show real metadata.

* Issue #138 — the ``make_version_info(...)`` call must be gated on
  ``is_windows = sys.platform == 'win32'``. The helper transitively
  imports ``pefile`` (Windows-only) via PyInstaller's
  ``win32.versioninfo`` module; calling it unconditionally crashed
  every macOS / Linux build with ``ModuleNotFoundError``.
"""

from __future__ import annotations

import re

import pytest

from . import conftest as _c


class TestWindowsIconWiredInAllSpecs:
    """All four PyInstaller specs must embed ``sethlans.ico`` via an
    ``icon=`` kwarg on their ``EXE(...)`` block (issue #96).

    Before this fix only ``launcher.spec`` set the icon. The other three
    (manager, worker, tray_helper) produced executables with PyInstaller's
    default generic icon, visible in Task Manager, the Details tab, and
    any shortcut pointing directly at them. These tests lock the wiring
    so it cannot silently drift back.
    """

    def test_windows_icon_file_exists(self) -> None:
        # The `icon=icon_path` kwarg guards on `ICON_WIN.exists()`, so
        # a missing .ico wouldn't break builds — it would just silently
        # drop the icon again. This sanity check keeps the source file
        # present on disk.
        assert _c.ICON_WIN.is_file(), (
            f"Expected Windows icon at {_c.ICON_WIN}; all four "
            "PyInstaller specs reference it for Windows builds (issue #96)."
        )

    @pytest.mark.parametrize("spec_name", list(_c.ALL_SPECS.keys()))
    def test_spec_defines_icon_win(self, spec_name: str) -> None:
        # Each spec must define an ``ICON_WIN`` path constant that points
        # at the sethlans.ico file. The constant is the anchor the
        # per-spec guard+assign+kwarg pattern hangs off.
        text = _c.ALL_SPECS[spec_name].read_text(encoding="utf-8")
        pattern = re.compile(
            r"ICON_WIN\s*=\s*.+?['\"]sethlans\.ico['\"]",
            re.MULTILINE,
        )
        assert pattern.search(text), (
            f"Expected {spec_name}.spec to define ICON_WIN pointing at "
            "packaging/windows/sethlans.ico (issue #96)."
        )

    @pytest.mark.parametrize("spec_name", list(_c.ALL_SPECS.keys()))
    def test_spec_guards_icon_path_on_file_existence(
        self, spec_name: str,
    ) -> None:
        # The guard pattern ``icon_path = str(ICON_WIN) if ICON_WIN.exists()
        # else None`` keeps the spec cross-platform: on macOS and Linux
        # builds the .ico is absent and PyInstaller would crash if passed
        # a missing path. Locking the guard prevents a future "just
        # remove the ternary" cleanup that breaks non-Windows builds.
        text = _c.ALL_SPECS[spec_name].read_text(encoding="utf-8")
        pattern = re.compile(
            r"icon_path\s*=\s*str\(ICON_WIN\)\s+if\s+ICON_WIN\.exists\(\)"
            r"\s+else\s+None",
            re.MULTILINE,
        )
        assert pattern.search(text), (
            f"Expected {spec_name}.spec to guard icon_path on "
            "ICON_WIN.exists() (issue #96)."
        )

    @pytest.mark.parametrize("spec_name", list(_c.ALL_SPECS.keys()))
    def test_exe_receives_icon_kwarg(self, spec_name: str) -> None:
        # The final wire-up: ``EXE(..., icon=icon_path, ...)`` must
        # appear so PyInstaller actually stamps the icon on the built
        # .exe. A drift-and-forget where the constant exists and the
        # guard exists but the EXE call drops ``icon=`` is the exact
        # regression this test exists to catch.
        text = _c.ALL_SPECS[spec_name].read_text(encoding="utf-8")
        pattern = re.compile(
            r"^\s*icon\s*=\s*icon_path\s*,\s*$",
            re.MULTILINE,
        )
        assert pattern.search(text), (
            f"Expected {spec_name}.spec's EXE() call to include "
            "`icon=icon_path,` so the built Windows executable carries "
            "the Sethlans icon (issue #96)."
        )


class TestExeVersionResourceEmbedded:
    """All four PyInstaller specs must wire a Windows VERSIONINFO
    resource into their ``EXE(...)`` block via the ``version=`` kwarg
    (issue #109).

    Before this fix the exes shipped with no PE VERSIONINFO resource, so
    Task Manager Details, File Properties, and SmartScreen dialogs fell
    back to generic filename-derived strings. The fix adds
    ``packaging/pyinstaller/version_info.make_version_info`` and passes
    its result into each spec's ``EXE(version=_version_resource, ...)``
    call. These tests lock that wiring so a future cleanup cannot drop
    the kwarg and silently re-break the branding.
    """

    @pytest.mark.parametrize("spec_name", list(_c.ALL_SPECS.keys()))
    def test_exe_receives_version_kwarg(self, spec_name: str) -> None:
        # The EXE(...) block is the last call before COLLECT(...) in
        # every spec. Do a directed search for ``version=`` between the
        # ``exe = EXE(`` opener and the ``coll = COLLECT(`` opener so
        # we match only inside the EXE call and not some earlier
        # incidental mention (e.g. in a comment block).
        text = _c.ALL_SPECS[spec_name].read_text(encoding="utf-8")
        exe_start = text.index("exe = EXE(")
        collect_start = text.index("coll = COLLECT(")
        assert exe_start < collect_start, (
            f"{spec_name}.spec should have exe = EXE(...) before "
            "coll = COLLECT(...)."
        )
        exe_body = text[exe_start:collect_start]
        pattern = re.compile(
            r"^\s*version\s*=\s*\S+\s*,\s*$",
            re.MULTILINE,
        )
        assert pattern.search(exe_body), (
            f"Expected {spec_name}.spec's EXE() call to include "
            "`version=...,` so the built Windows executable carries the "
            "Sethlans VERSIONINFO resource (issue #109)."
        )

    @pytest.mark.parametrize("spec_name", list(_c.ALL_SPECS.keys()))
    def test_spec_imports_version_info_helper(
        self, spec_name: str,
    ) -> None:
        # Defense-in-depth: the version= kwarg is only meaningful if the
        # helper is actually wired in. Lock the import line so a future
        # refactor that drops the import (and leaves a dangling
        # ``version=_version_resource,``) fails loudly here instead of
        # only at pyinstaller run time.
        text = _c.ALL_SPECS[spec_name].read_text(encoding="utf-8")
        assert "from version_info import make_version_info" in text, (
            f"{spec_name}.spec must import make_version_info from the "
            "packaging/pyinstaller/version_info.py helper (issue #109)."
        )


class TestVersionInfoCallGatedOnWindows:
    """All four PyInstaller specs must gate the ``make_version_info(...)``
    call on ``is_windows`` (issue #138).

    The helper transitively imports ``pefile`` via PyInstaller's
    ``win32.versioninfo`` module. ``pefile`` is a Windows-only PE-file
    parser and is not installed in the macOS/Linux PyInstaller deps.
    Calling the helper unconditionally crashed the macOS and Linux
    Manager / Worker / Launcher / Tray Helper builds with
    ``ModuleNotFoundError: No module named 'pefile'`` at PyInstaller
    startup. The fix wraps the call in
    ``... if is_windows else None`` (EXE() accepts ``version=None``
    cleanly on every platform) and locks it here.

    Regression introduced by commit 601cffe ("Embed Windows VERSIONINFO
    on every PyInstaller exe").
    """

    @pytest.mark.parametrize("spec_name", list(_c.ALL_SPECS.keys()))
    def test_make_version_info_call_is_windows_gated(
        self, spec_name: str,
    ) -> None:
        # Match a ``make_version_info(...)`` invocation followed (within
        # a small whitespace/newline window) by ``if is_windows``. The
        # ``re.DOTALL`` flag lets ``.`` cross newlines so the multi-line
        # ternary form is accepted:
        #
        #     _version_resource = (
        #         make_version_info('run_x.exe', 'run_x')
        #         if is_windows else None
        #     )
        #
        # And the single-line form is also accepted:
        #
        #     _version_resource = make_version_info(...) if is_windows else None
        text = _c.ALL_SPECS[spec_name].read_text(encoding="utf-8")
        pattern = re.compile(
            r"make_version_info\([^)]*\)\s*\)?\s*if\s+is_windows\b",
            re.DOTALL,
        )
        assert pattern.search(text), (
            f"Expected {spec_name}.spec to gate its make_version_info(...) "
            "call on `is_windows` so macOS/Linux PyInstaller builds do "
            "not eagerly import the Windows-only `pefile` module "
            "(issue #138)."
        )

    @pytest.mark.parametrize("spec_name", list(_c.ALL_SPECS.keys()))
    def test_is_windows_defined_before_call(
        self, spec_name: str,
    ) -> None:
        # The gate is meaningless if ``is_windows`` is undefined at the
        # point of call — PyInstaller would raise NameError instead of
        # ModuleNotFoundError. Lock that the variable assignment
        # appears before the make_version_info(...) invocation.
        text = _c.ALL_SPECS[spec_name].read_text(encoding="utf-8")
        assign_match = re.search(
            r"^is_windows\s*=\s*sys\.platform\s*==\s*['\"]win32['\"]",
            text,
            re.MULTILINE,
        )
        assert assign_match, (
            f"{spec_name}.spec must define `is_windows = sys.platform "
            "== 'win32'` so the make_version_info(...) gate is "
            "evaluable (issue #138)."
        )
        # Find the LAST occurrence (the call site, not the import).
        call_idx = text.rindex("make_version_info(")
        assert assign_match.start() < call_idx, (
            f"{spec_name}.spec must define `is_windows` BEFORE the "
            "make_version_info(...) call site so the gate evaluates "
            "without NameError (issue #138)."
        )
