# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher-specific PyInstaller spec regression tests, plus the
cross-spec VERSION-file copy guard.

Locks in the fixes for:

* Issue #100 — ``launcher.spec`` must declare
  ``sethlans_manager.caddy_template`` as a hidden import so the frozen
  launcher can template the manager Caddyfile.

* Issue #101 — ``launcher.spec`` must bundle
  ``workers.multicast_broadcaster``. Before this fix, the frozen
  launcher's ``BroadcasterSupervisor`` crash-looped every ~200 ms with
  ``ModuleNotFoundError: No module named 'workers'`` because
  PyInstaller's static analyzer never discovered the module: it was
  neither declared as a hidden import nor was ``manager/`` on
  ``pathex``. The runtime ``sys.path`` hack in
  ``launcher/broadcaster_supervisor.py`` only helps source mode — a
  frozen bundle ships only modules PyInstaller statically resolved.

* Issue #106 — both ``launcher.spec`` and ``tray_helper.spec`` must
  bundle the repo-root ``VERSION`` file into the frozen contents dir
  (``sys._MEIPASS``) so ``shared.version.get_version()`` can read it
  at runtime. Without the spec datas wiring, the splash surfaces a
  stale hardcoded version.
"""

from __future__ import annotations

import re

import pytest


class TestLauncherSpecBundlesWorkersBroadcaster:
    """``launcher.spec`` carries the frozen-launcher's hidden imports
    and the ``manager/`` pathex needed to find the workers package.
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
