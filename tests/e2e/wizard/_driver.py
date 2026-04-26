# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard hand-off E2E driver — runs ``run_wizard_mode`` in a subprocess.

The driver imports :func:`launcher.wizard_orchestration.run_wizard_mode`
and invokes it with a stubbed component spawner. The spawner spawns:

* The real wizard via ``wizard/run_wizard.py`` for ``component="wizard"``.
* A mock runtime (this directory's ``_mock_runtime.py``) for
  ``component in ("manager", "worker")``. The mock binds the FR-W14a
  hardcoded probe port for that topology and serves
  ``GET /api/health/`` if ``--runtime-mode=health-ok`` is set, or exits
  immediately if ``--runtime-mode=fail-immediately`` is set.

Why a separate driver script (vs. invoking ``launcher/run_launcher.py``
directly): the real launcher always spawns the tray subprocess (PySide6
dependency, brittle in CI / headless), drives data-dir resolution from
``LOCALAPPDATA`` / ``XDG_DATA_HOME`` (different per OS), and runs a
splash screen unless ``--no-browser`` is set. The driver bypasses all
of that and exercises the wizard hand-off lifecycle at the
orchestration boundary — the same boundary the real launcher uses.

Lifecycle: a SIGTERM (or SIGINT) handler terminates ALL spawned
children before the driver exits. This is critical because Windows
does not propagate signals to child processes — without explicit
cleanup, mock runtimes / wizards from prior tests leak into the host
and conflict with subsequent tests' port-bind needs.

Exit codes:
* 0 — wizard hand-off + runtime port-bind both succeeded (FR-L7).
* 1 — wizard hand-off failed or runtime port-bind timed out (FR-L7b).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure the project root is on sys.path so the launcher / wizard /
# shared imports resolve when this driver runs as a child process
# without inheriting pytest's modified sys.path.
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Imports must come AFTER the sys.path insertion above.
from launcher import wizard_orchestration  # noqa: E402
from launcher.component_paths import find_component_exe  # noqa: E402

logger = logging.getLogger("wizard_e2e_driver")

_MOCK_RUNTIME = _HERE.parent / "_mock_runtime.py"


class _ChildTracker:
    """Track spawned children so the driver can SIGTERM them on exit."""

    def __init__(self, runtime_mode: str, runtime_port: int) -> None:
        self.runtime_mode = runtime_mode
        self.runtime_port = runtime_port
        self.children: list[subprocess.Popen] = []

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
        return proc

    def terminate_all(self, grace_seconds: float = 5.0) -> None:
        """SIGTERM all live children, escalate to kill after *grace_seconds*."""
        for proc in self.children:
            _politely_terminate(proc)
        for proc in self.children:
            _wait_or_kill(proc, grace_seconds)
        for proc in self.children:
            _cleanup_log_files(proc)


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


def _install_cleanup_signals(tracker: _ChildTracker) -> None:
    """Install SIGTERM/SIGINT handlers that terminate spawned children.

    Called once at startup. The handlers swallow the signal after
    cleanup so the driver can exit deterministically with rc=143
    (SIGTERM convention) — but pytest only checks `rc != 0`, so the
    exact value doesn't matter.
    """
    def _handler(signum, frame):  # noqa: ARG001
        logger.info(
            "driver received signal %d; terminating %d children",
            signum, len(tracker.children),
        )
        tracker.terminate_all()
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _bootstrap_first_run(data_dir: Path) -> Path:
    """Minimal ``manager.ini`` write — mock runtime doesn't read it.

    The launcher's real ``_bootstrap_first_run`` writes a ``manager.ini``
    so the Django runtime can boot. The mock runtime ignores that file,
    but we still call this for parity with the production code path.
    """
    manager_data = data_dir / "manager"
    manager_data.mkdir(parents=True, exist_ok=True)
    return manager_data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wizard E2E driver.")
    parser.add_argument(
        "--data-dir", required=True,
        help="Per-test isolated data directory.",
    )
    parser.add_argument(
        "--runtime-mode",
        choices=("health-ok", "fail-immediately"),
        default="health-ok",
        help="Behaviour of the mock runtime spawned by the driver.",
    )
    parser.add_argument(
        "--runtime-port", type=int, default=8080,
        help="Port the mock runtime binds (must match FR-W14a probe URL).",
    )
    parser.add_argument(
        "--idle-timeout", type=float,
        default=wizard_orchestration.DEFAULT_WIZARD_IDLE_TIMEOUT,
        help="Override the FR-L7a idle timeout (seconds).",
    )
    parser.add_argument(
        "--setup-token", default=None,
        help="Pin the wizard's setup token to a known value (option-b in "
             "the spec). Lets the test client authenticate without "
             "racing the wizard's FR-W6 immediate-unlink.",
    )
    parser.add_argument(
        "--ipc-secret", default=None,
        help="Pin the wizard's IPC HMAC secret (hex-encoded). Lets the "
             "test verify the .wizard_done marker via the same secret.",
    )
    return parser


def _apply_pinning(args: argparse.Namespace) -> None:
    """Monkey-patch wizard_orchestration token/secret generators if pinned."""
    if args.setup_token is not None:
        token = args.setup_token

        def _fixed_token() -> str:
            return token
        wizard_orchestration.generate_setup_token = _fixed_token  # type: ignore[assignment]
    if args.ipc_secret is not None:
        secret_bytes = bytes.fromhex(args.ipc_secret)

        def _fixed_secret() -> bytes:
            return secret_bytes
        wizard_orchestration.generate_ipc_secret = _fixed_secret  # type: ignore[assignment]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _build_parser().parse_args(argv)
    _apply_pinning(args)

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Mimic the launcher's argparse Namespace shape for run_wizard_mode.
    fake_args = argparse.Namespace(no_browser=True, print_url=False)

    tracker = _ChildTracker(args.runtime_mode, args.runtime_port)
    _install_cleanup_signals(tracker)

    try:
        rc = wizard_orchestration.run_wizard_mode(
            data_dir=data_dir,
            args=fake_args,
            bootstrap_first_run=_bootstrap_first_run,
            start_component=tracker.start_component,
            on_manager_ready=None,
            idle_timeout=args.idle_timeout,
        )
    finally:
        # FR-L13: hand_off_to_runtime keeps the runtime alive for the
        # production launcher; in the test driver we want a clean exit
        # so subsequent tests can rebind the same ports. SIGTERM all
        # children regardless of rc.
        tracker.terminate_all()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
