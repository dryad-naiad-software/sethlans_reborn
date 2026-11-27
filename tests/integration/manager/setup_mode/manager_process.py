# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A real normal ``run_manager`` start (no ``--setup``) for integration tests.

It runs the whole start: data-folder check, certificate, ``migrate`` and
``collectstatic`` subprocesses, the first-start step, then Waitress on two
free loopback ports. :meth:`ManagerProcess.start` returns once
``/api/health/`` answers 200, or raises with the output when the child
exits first. Output goes to files, and :meth:`ManagerProcess.stop` always
ends the child.
"""
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .setup_process import REPO_ROOT, RUN_MANAGER, distinct_free_ports, request

START_TIMEOUT_S = 180.0  # migrate + collectstatic + a new certificate on a cold start
POLL_S = 0.2


@dataclass
class ManagerProcess:
    """One normal-start child; ``env`` is its full environment (ports are added here)."""

    folder: Path
    env: dict
    proc: Optional[subprocess.Popen] = None
    public_port: int = 0
    internal_port: int = 0
    _outputs: tuple = ()

    def start(self) -> "ManagerProcess":
        self.public_port, self.internal_port = distinct_free_ports(2)
        env = dict(self.env)
        env.update({"SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC": str(self.public_port),
                    "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL": str(self.internal_port)})
        out = open(self.folder / "manager.stdout.log", "wb")
        err = open(self.folder / "manager.stderr.log", "wb")
        self._outputs = (out, err)
        self.proc = subprocess.Popen(  # nosec B603 - fixed argv
            [sys.executable, str(RUN_MANAGER)], cwd=str(REPO_ROOT), env=env,
            stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        self._wait_serving()
        return self

    def _wait_serving(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                code = self.proc.wait(timeout=POLL_S)
            except subprocess.TimeoutExpired:
                code = None
            if code is not None:
                raise RuntimeError(f"the manager exited {code} before serving\n{self.output()}")
            try:
                if request(self.public_port, "GET", "/api/health/", timeout=5).status == 200:
                    return
            except OSError:
                pass
        raise TimeoutError(f"the manager did not serve on {self.public_port}\n{self.output()}")

    def output(self) -> str:
        for handle in self._outputs:
            handle.flush()
        parts = []
        for name in ("manager.stdout.log", "manager.stderr.log"):
            path = self.folder / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - a stuck kill
                pass
        for handle in self._outputs:
            handle.close()
        self._outputs = ()
