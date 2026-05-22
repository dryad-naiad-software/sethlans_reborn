# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #192 resolver smoke check for ``tools/wizard_smoke.py``.

Split out of ``tools/_wizard_smoke_helpers.py`` so both files stay
under the 300-line project cap (CLAUDE.md). Owns the launcher-
perspective harness that exercises
:func:`launcher.manager_exe_resolver.manager_exe` from a stubbed
launcher caller, plus the NF-9 assertion that the bundled
``shared.frozen_paths`` exports ``get_install_root``.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys


def err(msg: str) -> None:
    """stderr print shortcut.

    Inlined from :mod:`_wizard_smoke_helpers` so this module imports
    cleanly without a sibling ``tools/`` entry on ``sys.path`` — the
    unit-test agent (and any other importer that doesn't go through
    ``wizard_smoke.py``) gets a self-contained module.
    """
    print(msg, file=sys.stderr)


def _harness_source() -> str:
    """Return the Python source executed by :func:`check_manager_exe_resolver`.

    The harness runs in source mode (invoked via ``python -c``) but
    stubs ``sys.frozen = True`` and ``sys.executable`` so the resolver
    believes it is being called from the bundled launcher.

    Two things exercised in one process:

    1. **NF-9** — assert that the bundled :mod:`shared.frozen_paths`
       exports ``get_install_root``. If CI ever ships a stale copy
       without the new function, the attribute lookup raises and the
       harness exits non-zero before reaching :func:`manager_exe`.
    2. **FR-TESTS8** — call
       :func:`launcher.manager_exe_resolver.manager_exe` with
       ``sys.executable`` pointing at the launcher binary and assert
       it returns the manager binary path without raising. Launcher-
       perspective regression guard for #192.
    """
    return (
        "import os, sys\n"
        "sys.executable = os.environ['SETHLANS_LAUNCHER_EXE']\n"
        "sys.frozen = True\n"
        # NF-9: bundled shared.frozen_paths must carry get_install_root.
        "from shared.frozen_paths import get_install_root\n"
        "_ = get_install_root  # noqa: F401\n"
        # FR-TESTS8: launcher-perspective resolver call.
        "from launcher.manager_exe_resolver import manager_exe\n"
        "print(manager_exe())\n"
    )


def _find_component_exe(
    dist_root: pathlib.Path, component: str, exe_name: str,
) -> pathlib.Path | None:
    """Find ``exe_name`` under ``dist_root/<component>/``, return first hit."""
    component_root = dist_root / component
    if not component_root.is_dir():
        return None
    candidates = list(component_root.rglob(exe_name))
    return candidates[0] if candidates else None


def _build_harness_env(launcher_exe: pathlib.Path) -> dict[str, str]:
    """Return environment for the harness subprocess.

    Sets ``SETHLANS_LAUNCHER_EXE`` (the stubbed ``sys.executable``
    value) and prepends the repo root to ``PYTHONPATH`` so the
    harness can import the source-tree ``shared`` and ``launcher``
    packages. Per FR-TESTS8 implementation note this is the unit-
    level driver — the harness exercises source code with the bundle
    path as a stubbed ``sys.executable``, not a frozen launcher.
    """
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["SETHLANS_LAUNCHER_EXE"] = str(launcher_exe)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{existing_pp}" if existing_pp
        else str(repo_root)
    )
    return env


def check_manager_exe_resolver(bundle: pathlib.Path) -> bool:
    """Issue #192 (FR-TESTS8 + NF-9): exercise resolver from launcher view.

    Locates ``dist/launcher/run_launcher[.exe]`` and
    ``dist/manager/run_manager[.exe]`` (siblings of the wizard
    bundle). Spawns the Python harness from :func:`_harness_source`
    that stubs ``sys.executable`` to the launcher binary and calls
    :func:`launcher.manager_exe_resolver.manager_exe`. The pre-#192
    code raised ``FileNotFoundError`` in this exact configuration
    (bounds check anchored at ``bin/launcher/`` instead of ``bin/``),
    so a green run here confirms the fix is in the build.

    Skips with a printed message (not a failure) if either the
    launcher or manager bundle is absent — wizard-only dev iterations
    must still pass this harness without those sibling bundles built.

    Also exercises NF-9 (bundled ``shared.frozen_paths`` exports
    ``get_install_root``) — see :func:`_harness_source`.
    """
    dist_root = bundle.parent
    launcher_exe_name = (
        "run_launcher.exe" if sys.platform == "win32" else "run_launcher"
    )
    manager_exe_name = (
        "run_manager.exe" if sys.platform == "win32" else "run_manager"
    )
    launcher_exe = _find_component_exe(
        dist_root, "launcher", launcher_exe_name,
    )
    manager_exe_path = _find_component_exe(
        dist_root, "manager", manager_exe_name,
    )
    if launcher_exe is None or manager_exe_path is None:
        print(
            "#192 SKIPPED: launcher or manager bundle missing under "
            f"{dist_root} (looked for {launcher_exe_name} and "
            f"{manager_exe_name})"
        )
        return True
    expected_manager_exe = manager_exe_path.resolve()
    env = _build_harness_env(launcher_exe)
    print(f"#192 invoking resolver harness with launcher={launcher_exe}")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _harness_source()],
            capture_output=True, timeout=30, env=env,
        )
    except subprocess.TimeoutExpired:
        err("#192 FAILED: resolver harness timed out")
        return False
    if result.returncode != 0:
        err(
            f"#192 FAILED: resolver harness exit={result.returncode}\n"
            f"  stdout: {result.stdout!r}\n"
            f"  stderr: {result.stderr!r}"
        )
        return False
    if b"FileNotFoundError" in result.stderr:
        err(
            "#192 FAILED: resolver harness stderr contains "
            f"FileNotFoundError: {result.stderr!r}"
        )
        return False
    printed = result.stdout.decode("utf-8", errors="replace").strip()
    try:
        printed_path = pathlib.Path(printed).resolve()
    except (OSError, ValueError):
        err(f"#192 FAILED: harness printed unparseable path: {printed!r}")
        return False
    if printed_path != expected_manager_exe:
        err(
            "#192 FAILED: harness returned wrong manager exe "
            f"(got {printed_path}, expected {expected_manager_exe})"
        )
        return False
    print(f"#192 passed: resolver harness returned {printed_path}")
    return True
