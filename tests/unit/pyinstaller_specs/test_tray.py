# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tray-helper PyInstaller spec regression tests.

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
  producing a noisy "Library not found" warning at every Linux build.
  The tray only loads PNG icons, so the plugin is dead weight on every
  platform.
"""

from __future__ import annotations

import re

import pytest


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
