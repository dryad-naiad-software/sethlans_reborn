# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Child-process spawner + cleanup for the wizard E2E driver.

Split from ``_driver.py`` to keep both files under the 300-line limit.
:class:`ChildTracker` is the stub ``start_component`` the driver hands
to the real launcher orchestration: it spawns the real wizard for
``component="wizard"`` and this directory's ``_mock_runtime.py`` for
``component in ("manager", "worker")``. Every spawned child is tracked
so :meth:`ChildTracker.terminate_all` can reap it before the driver
exits — Windows does not propagate signals to children, so without
explicit cleanup mock runtimes / wizards would leak and block later
tests' port binds.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from launcher.component_paths import find_component_exe

logger = logging.getLogger("wizard_e2e_driver")

_MOCK_RUNTIME = Path(__file__).resolve().parent / "_mock_runtime.py"


class ChildTracker:
    """Track spawned children so the driver can SIGTERM them on exit."""

    def __init__(
        self, runtime_mode: str, runtime_port: int,
        on_spawn: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        self.runtime_mode = runtime_mode
        self.runtime_port = runtime_port
        self.children: list[subprocess.Popen] = []
        self._on_spawn = on_spawn

    def start_component(
        self, component: str, extra_args=None, env: dict | None = None,
    ) -> subprocess.Popen:
        """Stub-spawner matching ``launcher/run_launcher.py::_start_component``."""
        del extra_args
        if component == "wizard":
            exe = find_component_exe("wizard")
            cmd = [sys.executable, str(exe)]
        elif component in ("manager", "worker"):
            cmd = [
                sys.executable, str(_MOCK_RUNTIME),
                "--mode", self.runtime_mode,
                "--port", str(self.runtime_port),
            ]
        else:
            raise ValueError(f"driver: unknown component {component!r}")

        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)
        # CRITICAL: NEVER use subprocess.PIPE for child stdout/stderr on
        # Windows (4 KiB buffer → deadlock once full → child blocks on
        # write → can't process SIGTERM). Use NamedTemporaryFile so the
        # OS handles backpressure on disk, then read back in tests if a
        # child died unexpectedly.
        out_file = tempfile.NamedTemporaryFile(
            prefix=f"e2e_wizard_{component}_out_", suffix=".log",
            delete=False,
        )
        err_file = tempfile.NamedTemporaryFile(
            prefix=f"e2e_wizard_{component}_err_", suffix=".log",
            delete=False,
        )
        proc = subprocess.Popen(
            cmd, env=proc_env, stdout=out_file, stderr=err_file,
        )
        # Stash the file paths on the Popen so terminate_all + post-
        # mortem inspection can read them. Close our handles since
        # Popen has dup'd them into the child.
        proc._e2e_stdout_path = Path(out_file.name)  # type: ignore[attr-defined]
        proc._e2e_stderr_path = Path(err_file.name)  # type: ignore[attr-defined]
        out_file.close()
        err_file.close()
        self.children.append(proc)
        logger.info("spawned %s pid=%s", component, proc.pid)
        if self._on_spawn is not None:
            self._on_spawn(component, proc.pid)
        return proc

    def terminate_all(self, grace_seconds: float = 5.0) -> None:
        """SIGTERM all live children, escalate to kill after *grace_seconds*."""
        for proc in self.children:
            _politely_terminate(proc)
        for proc in self.children:
            _wait_or_kill(proc, grace_seconds)
        # Surface child stdout/stderr to the driver's own stderr so #156
        # diagnostics survive _cleanup_log_files. The driver's stderr is
        # captured by the test harness; child stderr otherwise dies with
        # the temp file.
        for proc in self.children:
            _dump_child_logs(proc)
        for proc in self.children:
            _cleanup_log_files(proc)


def _dump_child_logs(proc: subprocess.Popen) -> None:
    """Copy *proc*'s captured stdout/stderr onto the driver's stderr."""
    for stream_name, attr in (("stdout", "_e2e_stdout_path"),
                              ("stderr", "_e2e_stderr_path")):
        path = getattr(proc, attr, None)
        if path is None:
            continue
        try:
            content = Path(path).read_text(errors="replace")
        except OSError:
            continue
        if content.strip():
            sys.stderr.write(
                f"--- pid={proc.pid} {stream_name} ---\n{content}\n"
            )


def _cleanup_log_files(proc: subprocess.Popen) -> None:
    """Best-effort delete the temp stdout/stderr log files for *proc*."""
    for attr in ("_e2e_stdout_path", "_e2e_stderr_path"):
        path = getattr(proc, attr, None)
        if path is None:
            continue
        try:
            path.unlink()
        except (FileNotFoundError, OSError):
            pass


def _politely_terminate(proc: subprocess.Popen) -> None:
    """Send SIGTERM to *proc* if still alive; swallow expected errors."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError as exc:
        logger.warning("terminate() on pid=%s failed: %s", proc.pid, exc)


def _wait_or_kill(proc: subprocess.Popen, grace_seconds: float) -> None:
    """Wait *grace_seconds* for *proc* to exit; SIGKILL if it doesn't."""
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


__all__ = ["ChildTracker"]
