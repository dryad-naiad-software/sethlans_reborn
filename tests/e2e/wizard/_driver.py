# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard hand-off E2E driver — runs the launcher orchestration in a subprocess.

The driver calls :func:`launcher.main_dispatch._run_orchestration` — the
same wizard-mode → normal-mode dispatch the real launcher's ``main()``
uses. Since issue #203 that is a single path: ``run_wizard_mode`` runs
the wizard and ``hand_off_to_runtime`` (apply pipeline + wizard
teardown), then ``_run_orchestration`` falls through to
``run_normal_mode``, which spawns the runtime and owns the cold-boot
health check (``_await_cold_boot``) and the supervision loop.

The stub component spawner (:class:`_driver_children.ChildTracker`)
spawns:

* The real wizard via ``wizard/run_wizard.py`` for ``component="wizard"``.
* A mock runtime (this directory's ``_mock_runtime.py``) for
  ``component in ("manager", "worker")``. The mock binds the given port
  and serves ``GET /api/health/`` if ``--runtime-mode=health-ok`` is
  set, or exits immediately if ``--runtime-mode=fail-immediately`` is
  set.

Seams that need a real Django install or a second Caddy are stubbed in
:mod:`_driver_stubs`, which also records spawns, the hand-off rc and the
splash callbacks to ``<data_dir>/.e2e_driver_events.jsonl`` for the
tests to assert on. Once the runtime passes cold boot the driver
requests quit (standing in for the tray) so normal mode exits through
the user-quit cascade with rc=0.

Why a separate driver script (vs. invoking ``launcher/run_launcher.py``
directly): the real launcher always spawns the tray subprocess (PySide6
dependency, brittle in CI / headless), drives data-dir resolution from
``LOCALAPPDATA`` / ``XDG_DATA_HOME`` (different per OS), and runs a
splash screen unless ``--no-browser`` is set. The driver bypasses all
of that and exercises the hand-off lifecycle at the orchestration
boundary — the same boundary the real launcher uses.

Lifecycle: a SIGTERM (or SIGINT) handler terminates ALL spawned
children before the driver exits. This is critical because Windows
does not propagate signals to child processes — without explicit
cleanup, mock runtimes / wizards from prior tests leak into the host
and conflict with subsequent tests' port-bind needs.

Exit codes (``_run_orchestration``'s rc):
* 0 — hand-off succeeded and the runtime passed normal-mode cold boot.
* 1 — wizard hand-off failed, or the runtime failed cold boot
  (``fail_cold_boot`` → ``on_startup_failed``).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

# Ensure the project root is on sys.path so the launcher / wizard /
# shared imports resolve when this driver runs as a child process
# without inheriting pytest's modified sys.path.
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Imports must come AFTER the sys.path insertion above.
from launcher import main_dispatch, wizard_orchestration  # noqa: E402
from tests.e2e.wizard import _driver_stubs  # noqa: E402
from tests.e2e.wizard._driver_children import ChildTracker  # noqa: E402

logger = logging.getLogger("wizard_e2e_driver")

# Stand-in for the tray IPC secret ``_pre_orchestration_setup`` would
# generate. No tray runs, so no marker is ever signed with it.
_DRIVER_TRAY_SECRET = "e2e-driver-no-tray"


def _install_cleanup_signals(tracker: ChildTracker) -> None:
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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _build_parser().parse_args(argv)

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    events = _driver_stubs.DriverEvents(data_dir)
    _driver_stubs.apply_pinning(args.setup_token, args.ipc_secret)
    _driver_stubs.pin_idle_timeout(args.idle_timeout)
    _driver_stubs.stub_apply_pipeline(data_dir)
    _driver_stubs.stub_runtime_caddy()
    _driver_stubs.record_hand_off(events)

    # Mimic the launcher's argparse Namespace shape.
    fake_args = argparse.Namespace(no_browser=True, print_url=False)

    tracker = ChildTracker(
        args.runtime_mode, args.runtime_port, on_spawn=events.on_spawn,
    )
    _install_cleanup_signals(tracker)

    try:
        rc = main_dispatch._run_orchestration(
            data_dir, fake_args, None, _DRIVER_TRAY_SECRET,
            bootstrap_first_run=_bootstrap_first_run,
            start_component=tracker.start_component,
            on_cold_boot_ready=events.on_cold_boot_ready,
            on_startup_failed=events.on_startup_failed,
        )
    finally:
        # Normal mode already terminated the runtime on both the quit
        # cascade and the cold-boot failure path; this is the safety
        # net so subsequent tests can rebind the same ports. SIGTERM
        # all children regardless of rc.
        tracker.terminate_all()
    events.record("exit", rc=rc)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
