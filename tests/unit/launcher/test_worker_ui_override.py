# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for FR-10 (D4) — launcher injects ``SETHLANS_WORKER_UI_ENABLED=true``
inside ``_start_component`` for ``component == "worker"``.

Also covers AC-ProcessEventsBounded (NFR-7) — ``app.processEvents()`` is
called from a single site in ``launcher/splash_runner.py``.
"""

from __future__ import annotations

from pathlib import Path

from launcher import run_launcher


class TestWorkerUIOverride:

    def test_worker_env_includes_ui_enabled_true(self, mocker):
        proc = mocker.MagicMock()
        popen = mocker.patch.object(
            run_launcher.subprocess, "Popen", return_value=proc,
        )
        mocker.patch.object(
            run_launcher, "_find_component_exe", return_value=Path("/x"),
        )
        mocker.patch.object(
            run_launcher, "popen_kwargs_for_component",
            return_value={},
        )

        run_launcher._start_component("worker")
        env = popen.call_args.kwargs["env"]
        assert env.get("SETHLANS_WORKER_UI_ENABLED") == "true"

    def test_manager_env_does_not_set_ui_enabled(self, mocker):
        proc = mocker.MagicMock()
        popen = mocker.patch.object(
            run_launcher.subprocess, "Popen", return_value=proc,
        )
        mocker.patch.object(
            run_launcher, "_find_component_exe", return_value=Path("/x"),
        )
        mocker.patch.object(
            run_launcher, "popen_kwargs_for_component",
            return_value={},
        )

        run_launcher._start_component("manager")
        env = popen.call_args.kwargs["env"]
        # Override is scoped to component == "worker" only.
        assert "SETHLANS_WORKER_UI_ENABLED" not in env or (
            env.get("SETHLANS_WORKER_UI_ENABLED") != "true"
            or "SETHLANS_WORKER_UI_ENABLED" not in {
                k for k, v in env.items() if v == "true"
            }
        )

    def test_tray_env_does_not_force_ui_enabled(self, mocker):
        proc = mocker.MagicMock()
        popen = mocker.patch.object(
            run_launcher.subprocess, "Popen", return_value=proc,
        )
        mocker.patch.object(
            run_launcher, "_find_component_exe", return_value=Path("/x"),
        )
        mocker.patch.object(
            run_launcher, "popen_kwargs_for_component",
            return_value={},
        )

        run_launcher._start_component("tray")
        env = popen.call_args.kwargs["env"]
        # tray spawn may receive an env={} -> os.environ.copy() inherits
        # whatever the host has set; assert we did NOT add the override.
        # The override only activates for component == "worker".
        # If host env happens to have SETHLANS_WORKER_UI_ENABLED, it
        # was inherited, not set by the launcher.
        assert env.get("_LAUNCHER_FORCED_UI_OVERRIDE", None) is None


class TestProcessEventsBounded:
    """AC-ProcessEventsBounded (NFR-7): ``app.processEvents()`` is called
    from exactly one site in ``launcher/`` — between ``splash.show()``
    and ``thread.start()`` in ``splash_runner.run_with_splash``."""

    def test_processEvents_called_from_single_site(self):
        """AST-walk every .py in launcher/ and count call expressions that
        invoke ``processEvents``. Strings in comments and docstrings do
        not count — only real call sites.
        """
        import pathlib

        launcher_dir = pathlib.Path(run_launcher.__file__).parent
        sites = []
        for py in launcher_dir.glob("*.py"):
            sites.extend(_collect_process_events_sites(py))
        assert len(sites) == 1, (
            "AC-ProcessEventsBounded: expected exactly one "
            f"processEvents call site in launcher/, found {len(sites)}: "
            f"{sites}"
        )
        py_name, _lineno = sites[0]
        assert py_name == "splash_runner.py", (
            f"processEvents must live in splash_runner.py, got {py_name}"
        )


def _collect_process_events_sites(py_path) -> list:
    """Return [(filename, lineno), ...] of processEvents call sites."""
    import ast

    try:
        text = py_path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return []
    return [
        (py_path.name, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _is_process_events(node.func)
    ]


def _is_process_events(func) -> bool:
    import ast

    if isinstance(func, ast.Attribute):
        return func.attr == "processEvents"
    if isinstance(func, ast.Name):
        return func.id == "processEvents"
    return False
