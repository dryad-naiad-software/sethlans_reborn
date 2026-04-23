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
LAUNCHER_SPEC = SPEC_DIR / "launcher.spec"
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


@pytest.fixture(scope="module")
def launcher_spec_text() -> str:
    return LAUNCHER_SPEC.read_text(encoding="utf-8")


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


class TestLauncherSpecBundlesWorkersBroadcaster:
    """launcher.spec must bundle ``workers.multicast_broadcaster``
    (issue #101).

    Before this fix, the frozen launcher's ``BroadcasterSupervisor``
    crash-looped every ~200 ms with
    ``ModuleNotFoundError: No module named 'workers'`` because
    PyInstaller's static analyzer never discovered the module: it was
    neither declared as a hidden import nor was ``manager/`` on
    ``pathex``. The runtime ``sys.path`` hack in
    ``launcher/broadcaster_supervisor.py`` only helps source mode — a
    frozen bundle ships only modules PyInstaller statically resolved.
    These tests lock the spec wiring so the regression cannot return.
    """

    def test_caddy_template_in_hiddenimports(
        self, launcher_spec_text: str,
    ) -> None:
        # Issue #100 / TR-6: the manager Caddyfile renderer lives in
        # ``sethlans_manager.caddy_template`` and is pulled in via a
        # dynamic import inside
        # ``launcher.caddy_launcher._load_manager_renderer``. PyInstaller's
        # static analyzer cannot see that import, so the module must be
        # declared as a hidden import. Without this, the frozen
        # launcher raises ``RuntimeError: sethlans_manager.caddy_template
        # not importable`` the moment it tries to template the Caddyfile,
        # leaving the public TLS port unbound.
        assert (
            "'sethlans_manager.caddy_template'" in launcher_spec_text
            or '"sethlans_manager.caddy_template"' in launcher_spec_text
        ), (
            "launcher.spec must declare 'sethlans_manager.caddy_template' "
            "as a hidden import so the frozen launcher can template the "
            "manager Caddyfile (issue #100)."
        )

    def test_multicast_broadcaster_in_hiddenimports(
        self, launcher_spec_text: str,
    ) -> None:
        # The module name must appear as a quoted hidden import. We
        # deliberately match only the single named import — using
        # ``collect_submodules('workers')`` would pull Django-dependent
        # siblings (views, models, serializers) and break the build,
        # so the test also guards against that shortcut implicitly
        # (the assertion below on 'workers' not being excluded is a
        # separate defensive check).
        assert (
            "'workers.multicast_broadcaster'" in launcher_spec_text
            or '"workers.multicast_broadcaster"' in launcher_spec_text
        ), (
            "launcher.spec must declare 'workers.multicast_broadcaster' "
            "as a hidden import so the frozen launcher can import it "
            "for UDP discovery (issue #101)."
        )

    def test_manager_dir_referenced_in_pathex(
        self, launcher_spec_text: str,
    ) -> None:
        # PyInstaller's static analyzer needs ``manager/`` on pathex to
        # locate the ``workers`` package at build time. Accept either
        # the ``MANAGER_DIR`` constant or the equivalent
        # ``PROJECT_ROOT / 'manager'`` literal in the pathex argument
        # of Analysis(...).
        # Find the Analysis(...) call by anchoring on the opening
        # paren and scanning to the matching close. A simple regex
        # with non-greedy ``.*?`` stops at the first ``)`` — which is
        # inside ``str(LAUNCHER_DIR / 'run_launcher.py')`` — so we do
        # a directed search for the pathex kwarg between the
        # Analysis( opener and the first line that starts a new
        # top-level call (pyz = PYZ(...)).
        analysis_start = launcher_spec_text.find("Analysis(")
        pyz_start = launcher_spec_text.find("pyz = PYZ(")
        assert analysis_start != -1 and pyz_start > analysis_start, (
            "launcher.spec must contain an Analysis(...) call followed "
            "by pyz = PYZ(...)."
        )
        body = launcher_spec_text[analysis_start:pyz_start]
        pathex_match = re.search(
            r"pathex\s*=\s*\[(?P<pathex>[^\]]*)\]",
            body,
        )
        assert pathex_match, (
            "launcher.spec Analysis() must declare a pathex kwarg."
        )
        pathex_contents = pathex_match.group("pathex")
        has_constant = "MANAGER_DIR" in pathex_contents
        has_literal = re.search(
            r"PROJECT_ROOT\s*/\s*['\"]manager['\"]",
            pathex_contents,
        )
        assert has_constant or has_literal, (
            "launcher.spec pathex must include MANAGER_DIR (or the "
            "equivalent PROJECT_ROOT / 'manager' literal) so "
            "PyInstaller's static analyzer can locate the workers "
            "package at build time (issue #101)."
        )

    def test_branding_asset_included_in_datas(
        self, launcher_spec_text: str,
    ) -> None:
        # The startup splash (launcher/splash.py) loads the wordmark
        # via shared.frozen_paths.get_branding_dir(), which resolves
        # to sys._MEIPASS / 'branding' in frozen mode. PyInstaller
        # must therefore copy the PNG into that subdirectory of the
        # bundle. Locking the ``logo-text-dark.png`` + ``'branding'``
        # wire-up guards against a refactor that silently reverts to
        # ``datas=[]`` and ships a bundle with no wordmark (spec TR-5).
        assert "logo-text-dark.png" in launcher_spec_text, (
            "launcher.spec must reference 'logo-text-dark.png' in its "
            "datas= wiring so the frozen bundle ships the startup "
            "splash wordmark."
        )
        assert "'branding'" in launcher_spec_text, (
            "launcher.spec must place the wordmark into the 'branding' "
            "subdirectory (shared.frozen_paths.get_branding_dir() "
            "resolves to sys._MEIPASS / 'branding' in frozen mode)."
        )

    @pytest.mark.parametrize(
        "module",
        ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"],
    )
    def test_qt_hidden_imports_declared(
        self, launcher_spec_text: str, module: str,
    ) -> None:
        # PySide6 ships its own PyInstaller hook, but the launcher
        # previously had no Qt deps; being explicit here makes the
        # build deterministic and documents the splash dependency.
        assert (
            f"'{module}'" in launcher_spec_text
            or f'"{module}"' in launcher_spec_text
        ), (
            f"launcher.spec must declare {module!r} as a hidden import "
            "so the frozen launcher can load the startup splash."
        )

    def test_workers_not_in_excludes(
        self, launcher_spec_text: str,
    ) -> None:
        # Defense-in-depth: a future cleanup pass adding 'workers' to
        # the excludes list would silently re-break the bundle even
        # with the hidden import declared. The launcher's excludes
        # list intentionally drops Django, DRF, PIL, psutil, and
        # cryptography — but 'workers' (the manager's Django app
        # package, only the stdlib-pure multicast_broadcaster
        # submodule is used) must never join that list.
        excludes_match = re.search(
            r"excludes\s*=\s*\[(?P<excludes>[^\]]*)\]",
            launcher_spec_text,
            re.DOTALL,
        )
        assert excludes_match, (
            "launcher.spec Analysis() must declare an excludes kwarg."
        )
        excludes_contents = excludes_match.group("excludes")
        assert "'workers'" not in excludes_contents, (
            "launcher.spec must NOT list 'workers' in excludes — doing "
            "so would drop workers.multicast_broadcaster from the "
            "frozen bundle and reintroduce issue #101."
        )
        assert '"workers"' not in excludes_contents, (
            "launcher.spec must NOT list \"workers\" in excludes — "
            "doing so would drop workers.multicast_broadcaster from "
            "the frozen bundle and reintroduce issue #101."
        )


class TestBundlesVersionFile:
    """Both the launcher and tray_helper specs must bundle the repo-root
    ``VERSION`` file into the frozen contents dir (``sys._MEIPASS``)
    so ``shared.version.get_version()`` can read it at runtime.

    Issue #106: before this fix the launcher had ``__version__ = "0.1.0"``
    hardcoded and the splash surfaced a stale version. The fix routes
    the launcher through ``shared.version.get_version()`` which resolves
    to ``sys._MEIPASS / 'VERSION'`` in frozen mode — but only if the
    spec copies the file into the bundle. This test locks that wiring.
    """

    def test_launcher_spec_bundles_version_file(
        self, launcher_spec_text: str,
    ) -> None:
        assert "'VERSION'" in launcher_spec_text, (
            "launcher.spec must reference 'VERSION' in its datas= wiring "
            "so the frozen bundle ships the repo-root VERSION file "
            "(issue #106)."
        )

    def test_tray_spec_bundles_version_file(
        self, tray_spec_text: str,
    ) -> None:
        assert "'VERSION'" in tray_spec_text, (
            "tray_helper.spec must reference 'VERSION' in its datas= "
            "wiring so the frozen bundle ships the repo-root VERSION "
            "file (issue #106)."
        )
