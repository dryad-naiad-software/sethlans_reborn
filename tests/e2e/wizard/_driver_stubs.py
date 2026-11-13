# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Production-seam stubs + event recorder for the wizard E2E driver.

The driver runs the real :func:`launcher.main_dispatch._run_orchestration`
(wizard mode → hand-off → normal-mode fall-through, issue #203). These
helpers replace only the seams that need a real Django install or a
second Caddy, and record what the orchestration did so the pytest
process can assert on it after the driver exits:

* :func:`stub_apply_pipeline` — replaces ``run_apply_pipeline`` (Django
  ``migrate`` + ``apply_pending_setup``) with a writer of the
  ``.setup_complete`` sentinel. The real topology gate in
  ``run_apply_pipeline_if_needed`` is left in place.
* :func:`stub_runtime_caddy` — normal mode's Caddy supervisor is a
  no-op; the mock runtime binds the public port (8080) itself, so the
  launcher's cold-boot probe hits it directly.
* :class:`DriverEvents` — JSON-lines log of spawns, the hand-off rc and
  the splash callbacks (``cold_boot_ready`` / ``startup_failed``).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from launcher import (
    apply_pending_setup,
    orchestration,
    supervision,
    wizard_orchestration,
    wizard_runtime,
)

logger = logging.getLogger("wizard_e2e_driver")

EVENTS_FILENAME = ".e2e_driver_events.jsonl"


def read_events(data_dir: Path) -> list[dict]:
    """Return the driver's recorded events (empty list if none)."""
    path = Path(data_dir) / EVENTS_FILENAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return []
    return [json.loads(line) for line in lines if line.strip()]


class DriverEvents:
    """Append-only event log shared by the driver's stubs + callbacks."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / EVENTS_FILENAME
        self._lock = threading.Lock()
        self.handed_off = False

    def record(self, event: str, **fields) -> None:
        line = json.dumps({"event": event, **fields}, sort_keys=True)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        logger.info("driver event: %s", line)

    def on_spawn(self, component: str, pid: int) -> None:
        self.record("spawn", component=component, pid=pid)

    def on_cold_boot_ready(self) -> None:
        """Splash success callback (fires once per cold-boot phase).

        Before hand-off it marks the wizard becoming healthy. After
        hand-off it marks the runtime passing normal-mode cold boot;
        the driver then plays the tray's part and requests quit so
        ``run_normal_mode`` leaves its supervision loop via the
        user-quit cascade (rc=0) instead of running forever.
        """
        phase = "runtime" if self.handed_off else "wizard"
        self.record("cold_boot_ready", phase=phase)
        if self.handed_off:
            supervision.get_quit_requested_event().set()

    def on_startup_failed(self, reason: str, detail: str) -> None:
        self.record("startup_failed", reason=reason, detail=detail)


def stub_apply_pipeline(data_dir: Path) -> None:
    """Replace the Django apply pipeline with a ``.setup_complete`` write.

    The suite never provisions a Django DB or ``pending_setup.json``.
    In production ``apply_pending_setup`` writes ``.setup_complete``,
    which ``_run_orchestration`` requires before falling through to
    normal mode; mirror only that side effect.
    """
    sentinel = Path(data_dir) / ".setup_complete"

    def _fake_run_apply_pipeline(pipeline_data_dir):
        del pipeline_data_dir
        sentinel.write_text("{}", encoding="utf-8")
        return True, ""

    apply_pending_setup.run_apply_pipeline = _fake_run_apply_pipeline  # type: ignore[assignment]


def stub_runtime_caddy() -> None:
    """No-op normal mode's Caddy supervisor (the mock binds 8080 itself)."""
    def _no_caddy(manager_data):
        logger.info("driver: skipping runtime Caddy for %s", manager_data)

    orchestration.start_caddy_supervisor = _no_caddy  # type: ignore[assignment]


def record_hand_off(events: DriverEvents) -> None:
    """Wrap ``hand_off_to_runtime`` to record its rc + flip the phase."""
    real = wizard_runtime.hand_off_to_runtime

    def _recording_hand_off(*args, **kwargs):
        rc = real(*args, **kwargs)
        events.handed_off = True
        events.record("hand_off", rc=rc)
        return rc

    wizard_runtime.hand_off_to_runtime = _recording_hand_off  # type: ignore[assignment]


def pin_idle_timeout(idle_timeout: float) -> None:
    """Thread the driver's ``--idle-timeout`` into ``run_wizard_mode``.

    ``_run_orchestration`` does not expose the idle timeout, so wrap the
    module attribute it calls through.
    """
    real = wizard_orchestration.run_wizard_mode

    def _with_idle_timeout(*args, **kwargs):
        kwargs.setdefault("idle_timeout", idle_timeout)
        return real(*args, **kwargs)

    wizard_orchestration.run_wizard_mode = _with_idle_timeout  # type: ignore[assignment]


def apply_pinning(setup_token: str | None, ipc_secret_hex: str | None) -> None:
    """Pin the wizard's setup token / IPC secret to test-known values."""
    if setup_token is not None:
        def _fixed_token() -> str:
            return setup_token
        wizard_orchestration.generate_setup_token = _fixed_token  # type: ignore[assignment]
    if ipc_secret_hex is not None:
        secret_bytes = bytes.fromhex(ipc_secret_hex)

        def _fixed_secret() -> bytes:
            return secret_bytes
        wizard_orchestration.generate_ipc_secret = _fixed_secret  # type: ignore[assignment]


__all__ = [
    "EVENTS_FILENAME",
    "DriverEvents",
    "apply_pinning",
    "pin_idle_timeout",
    "read_events",
    "record_hand_off",
    "stub_apply_pipeline",
    "stub_runtime_caddy",
]
