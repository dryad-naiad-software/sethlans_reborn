# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""From-source script-mode invocation regression test for the tray helper.

Issue #178: ``python shared/run_tray.py`` failed with
``ModuleNotFoundError: No module named 'shared'`` because the entry
point's lazy ``from shared.tray import app`` resolved against
``sys.path[0]=shared/`` (the script's parent directory) instead of the
project root. PyInstaller-frozen mode is unaffected — the bootloader
handles sys.path; only from-source invocations (the launcher's
``find_component_exe('tray')`` returning ``shared/run_tray.py``, or
ad-hoc ``python shared/run_tray.py`` debugging) trip it.

Sibling of #177's launcher fix; same shape, different file.

Strategy: spawn ``python shared/run_tray.py`` from an unrelated cwd via
``runpy.run_path`` so we can trap the import sequence without launching
PySide6's event loop. The bootstrap fires at module import time, so a
successful ``runpy.run_path(... run_name='__not_main__')`` (an unused
sentinel that prevents ``main()`` from being invoked via the
``if __name__ == '__main__'`` guard) proves the bootstrap fixed the
project-root import path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TRAY_ENTRY = PROJECT_ROOT / "shared" / "run_tray.py"


@pytest.mark.skipif(
    getattr(sys, "frozen", False),
    reason="Frozen mode: PyInstaller bootloader handles sys.path; "
           "the from-source bootstrap is not exercised.",
)
def test_tray_imports_cleanly_from_arbitrary_cwd(tmp_path):
    """Issue #178: ``python shared/run_tray.py`` must not ModuleNotFound.

    Asserts the bootstrap inserts the project root into ``sys.path``
    before the lazy ``from shared.tray import app`` line runs.

    We invoke the entry via ``runpy.run_path`` with a non-``__main__``
    ``run_name`` so the trailing ``if __name__ == '__main__': sys.exit(
    main())`` guard does NOT fire. This exercises the module-import-
    time bootstrap (which IS what the issue is about) without bringing
    up the PySide6 tray window. ``main()`` itself contains the lazy
    ``from shared.tray import app`` — to verify that lazy import also
    resolves, we additionally call ``main`` indirectly via a probe that
    monkey-patches ``shared.tray.app.main`` to a no-op before invoking.
    """
    # Probe 1: module-import-time bootstrap.
    bootstrap_probe = (
        "import runpy; "
        f"runpy.run_path({str(TRAY_ENTRY)!r}, run_name='__not_main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", bootstrap_probe],
        cwd=str(tmp_path),  # cwd != project_root keeps sys.path[0]=tmp_path
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "ModuleNotFoundError" not in combined, combined
    assert "No module named 'shared'" not in combined, combined
    assert result.returncode == 0, (
        f"runpy probe exited rc={result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


@pytest.mark.skipif(
    getattr(sys, "frozen", False),
    reason="Frozen mode: PyInstaller bootloader handles sys.path.",
)
def test_tray_lazy_import_resolves_from_arbitrary_cwd(tmp_path):
    """The lazy ``from shared.tray import app`` inside ``main()`` resolves.

    Stubs ``shared.tray.app.main`` with a no-op so the test does not
    bring up a PySide6 tray window, then invokes ``main()`` and asserts
    rc=0 with no ``ModuleNotFoundError``. Without the bootstrap added in
    #178, the ``from shared.tray import app`` line throws before the
    stub can take effect (because the module-level bootstrap is what
    makes ``shared.tray`` resolvable in the first place).
    """
    probe = (
        "import sys, types; "
        # Pre-stub shared.tray.app so the lazy import inside main()
        # gets a no-op rather than launching PySide6.
        "stub = types.ModuleType('shared.tray.app'); "
        "stub.main = lambda: None; "
        # We rely on the bootstrap inside run_tray.py to put the project
        # root on sys.path FIRST so ``shared`` resolves to the real
        # package; then we override only the deepest leaf module.
        f"import runpy; "
        f"mod_globals = runpy.run_path("
        f"  {str(TRAY_ENTRY)!r}, run_name='__not_main__'); "
        # After run_path, the bootstrap has run and ``shared`` is
        # resolvable. Now stub the leaf and call main().
        "sys.modules['shared.tray.app'] = stub; "
        "rc = mod_globals['main'](); "
        "sys.exit(rc)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "ModuleNotFoundError" not in combined, combined
    assert "No module named 'shared'" not in combined, combined
    assert result.returncode == 0, (
        f"main() probe exited rc={result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
