# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for the PyInstaller spec files.

Locks in the fixes for:

* Issue #90 — ``requirements-build.txt`` must include the runtime HTTP
  stack (``requests`` + ``urllib3``) and ``psutil`` because
  ``packaging/pyinstaller/tray_helper.spec`` declares them as hidden
  imports. PyInstaller's hidden-import resolver still requires the
  modules to be importable from the build venv to walk their submodule
  graphs and bundle the right files; without these pins the build emits
  ``ERROR: Hidden import 'requests' not found`` and the resulting tray
  bundle ``ModuleNotFoundError``s at first poll.

* Issue #91 — the tray bundle must not ship PySide6's ``libqtiff``
  image-format plugin. It hard-depends on ``libtiff.so.5`` but
  Ubuntu 22.04+ / Debian 12+ / Fedora 36+ ship ``libtiff.so.6``,
  producing a noisy "Library not found" warning at every Linux
  build. The tray only loads PNG icons, so the plugin is dead
  weight on every platform.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_REQS = REPO_ROOT / "requirements-build.txt"
SPEC_DIR = REPO_ROOT / "packaging" / "pyinstaller"
TRAY_SPEC = SPEC_DIR / "tray_helper.spec"
ICON_WIN = REPO_ROOT / "packaging" / "windows" / "sethlans.ico"

ALL_SPECS = {
    "launcher": SPEC_DIR / "launcher.spec",
    "manager": SPEC_DIR / "manager.spec",
    "worker": SPEC_DIR / "worker.spec",
    "tray_helper": TRAY_SPEC,
}


@pytest.fixture(scope="module")
def build_reqs_text() -> str:
    return BUILD_REQS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tray_spec_text() -> str:
    return TRAY_SPEC.read_text(encoding="utf-8")


class TestBuildRequirementsTrayDeps:
    """requirements-build.txt must declare the tray runtime deps."""

    @pytest.mark.parametrize(
        "package, version",
        [
            ("requests", "2.32.4"),
            ("urllib3", "2.5.0"),
            ("psutil", "7.0.0"),
        ],
    )
    def test_pinned_dep_present(
        self, build_reqs_text: str, package: str, version: str,
    ) -> None:
        # Match a top-of-line `package==version` (no extras, no markers).
        # Accept whitespace either side of `==` for tolerance.
        pattern = re.compile(
            rf"^{re.escape(package)}\s*==\s*{re.escape(version)}\s*$",
            re.MULTILINE,
        )
        assert pattern.search(build_reqs_text), (
            f"{package}=={version} must be pinned in requirements-build.txt "
            "so PyInstaller can walk its submodule graph for the tray "
            "bundle's hidden imports (issue #90). Versions are kept in "
            "lockstep with worker/requirements.txt and "
            "manager/requirements.txt."
        )


class TestTraySpecHiddenImportsDeclared:
    """Sanity check: the spec actually declares the imports we just pinned."""

    @pytest.mark.parametrize(
        "module",
        ["requests", "requests.adapters", "urllib3", "psutil"],
    )
    def test_hidden_import_in_spec(
        self, tray_spec_text: str, module: str,
    ) -> None:
        # Quoted module name must appear in the hiddenimports list.
        assert (
            f"'{module}'" in tray_spec_text
            or f'"{module}"' in tray_spec_text
        ), (
            f"Expected tray_helper.spec to declare hidden import "
            f"{module!r}. The requirements-build.txt pin only matters "
            "if the spec actually references it (issue #90)."
        )


class TestTraySpecQtiffExclusion:
    """tray_helper.spec must drop the unused TIFF plugin (issue #91)."""

    def test_qtiff_filtered_from_binaries(self, tray_spec_text: str) -> None:
        # Match `a.binaries = [... 'qtiff' not in entry[0].lower() ...]`
        # tolerating whitespace and either single/double quotes.
        pattern = re.compile(
            r"a\.binaries\s*=\s*\[\s*\n?\s*entry for entry in a\.binaries"
            r"\s*\n?\s*if\s+['\"]qtiff['\"]\s+not in\s+"
            r"entry\[0\]\.lower\(\)",
            re.MULTILINE,
        )
        assert pattern.search(tray_spec_text), (
            "tray_helper.spec must filter the qtiff plugin out of "
            "a.binaries so the bundle does not ship libqtiff with its "
            "broken libtiff.so.5 dependency (issue #91)."
        )

    def test_qtiff_filtered_from_datas(self, tray_spec_text: str) -> None:
        # Same pattern but on a.datas. PyInstaller may classify the Qt
        # plugin under either binaries or datas depending on the host
        # OS / version, so both lists must be filtered.
        pattern = re.compile(
            r"a\.datas\s*=\s*\[\s*\n?\s*entry for entry in a\.datas"
            r"\s*\n?\s*if\s+['\"]qtiff['\"]\s+not in\s+"
            r"entry\[0\]\.lower\(\)",
            re.MULTILINE,
        )
        assert pattern.search(tray_spec_text), (
            "tray_helper.spec must also filter qtiff out of a.datas — "
            "PyInstaller's classification of Qt plugins varies across "
            "platforms (issue #91)."
        )

    def test_filter_runs_before_pyz_collect(
        self, tray_spec_text: str,
    ) -> None:
        # The qtiff filter MUST run after Analysis() and BEFORE the PYZ /
        # COLLECT steps that consume a.binaries and a.datas. Position
        # check guards against a future refactor moving the filter to
        # the wrong place.
        analysis_idx = tray_spec_text.index("a = Analysis(")
        binaries_filter_idx = tray_spec_text.index("a.binaries = [")
        pyz_idx = tray_spec_text.index("pyz = PYZ(")
        collect_idx = tray_spec_text.index("coll = COLLECT(")

        assert analysis_idx < binaries_filter_idx, (
            "qtiff filter must run AFTER Analysis() — Analysis populates "
            "a.binaries; filtering before it does nothing."
        )
        assert binaries_filter_idx < pyz_idx < collect_idx, (
            "qtiff filter must run BEFORE PYZ() and COLLECT() — those "
            "stages consume a.binaries; filtering after them is too late."
        )


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
        assert ICON_WIN.is_file(), (
            f"Expected Windows icon at {ICON_WIN}; all four PyInstaller "
            "specs reference it for Windows builds (issue #96)."
        )

    @pytest.mark.parametrize("spec_name", list(ALL_SPECS.keys()))
    def test_spec_defines_icon_win(self, spec_name: str) -> None:
        # Each spec must define an ``ICON_WIN`` path constant that points
        # at the sethlans.ico file. The constant is the anchor the
        # per-spec guard+assign+kwarg pattern hangs off.
        text = ALL_SPECS[spec_name].read_text(encoding="utf-8")
        pattern = re.compile(
            r"ICON_WIN\s*=\s*.+?['\"]sethlans\.ico['\"]",
            re.MULTILINE,
        )
        assert pattern.search(text), (
            f"Expected {spec_name}.spec to define ICON_WIN pointing at "
            "packaging/windows/sethlans.ico (issue #96)."
        )

    @pytest.mark.parametrize("spec_name", list(ALL_SPECS.keys()))
    def test_spec_guards_icon_path_on_file_existence(
        self, spec_name: str,
    ) -> None:
        # The guard pattern ``icon_path = str(ICON_WIN) if ICON_WIN.exists()
        # else None`` keeps the spec cross-platform: on macOS and Linux
        # builds the .ico is absent and PyInstaller would crash if passed
        # a missing path. Locking the guard prevents a future "just
        # remove the ternary" cleanup that breaks non-Windows builds.
        text = ALL_SPECS[spec_name].read_text(encoding="utf-8")
        pattern = re.compile(
            r"icon_path\s*=\s*str\(ICON_WIN\)\s+if\s+ICON_WIN\.exists\(\)"
            r"\s+else\s+None",
            re.MULTILINE,
        )
        assert pattern.search(text), (
            f"Expected {spec_name}.spec to guard icon_path on "
            "ICON_WIN.exists() (issue #96)."
        )

    @pytest.mark.parametrize("spec_name", list(ALL_SPECS.keys()))
    def test_exe_receives_icon_kwarg(self, spec_name: str) -> None:
        # The final wire-up: ``EXE(..., icon=icon_path, ...)`` must
        # appear so PyInstaller actually stamps the icon on the built
        # .exe. A drift-and-forget where the constant exists and the
        # guard exists but the EXE call drops ``icon=`` is the exact
        # regression this test exists to catch.
        text = ALL_SPECS[spec_name].read_text(encoding="utf-8")
        pattern = re.compile(
            r"^\s*icon\s*=\s*icon_path\s*,\s*$",
            re.MULTILINE,
        )
        assert pattern.search(text), (
            f"Expected {spec_name}.spec's EXE() call to include "
            "`icon=icon_path,` so the built Windows executable carries "
            "the Sethlans icon (issue #96)."
        )
