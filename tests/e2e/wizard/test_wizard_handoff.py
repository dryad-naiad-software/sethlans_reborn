# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end tests for the wizard hand-off lifecycle (Spec 1 / D2).

The driver runs the launcher's real ``_run_orchestration``: wizard mode
→ ``hand_off_to_runtime`` (apply pipeline + wizard teardown) → fall
through to ``run_normal_mode``, which spawns the runtime and owns its
cold-boot health check (issue #203).

Scenarios:

1. Manager-only happy path — the mock manager runtime passes normal-mode
   cold boot; the driver exits 0 via the user-quit cascade.
2. Worker-only happy path — same shape, ``worker_only`` topology, mock
   bound on port 8081; the launcher writes ``.setup_complete`` (#210).
3. Runtime fails after hand-off — mock runtime exits non-zero
   immediately; normal-mode cold boot detects it, fires
   ``on_startup_failed`` and the launcher exits 1.
4. Wizard exits without handshake — driver SIGTERM'd before the test
   client picks topology / fires done; driver returns non-zero.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from launcher import orchestration, wizard_ipc

from . import _driver_process as _dp
from . import _wizard_client as client
from ._driver_stubs import read_events

# #156: hosted GitHub macOS runners blow the launcher's 30 s cold-boot
# HEALTH_TIMEOUT (launcher/health_probe.py, used by normal mode's
# _await_cold_boot) on the mock-runtime cold-start path —
# `cryptography` import + RSA-2048 keygen + ssl.PROTOCOL_TLS_SERVER
# wrap takes longer than the budget on those VMs. The test passes on
# Linux + Windows hosted runners, on the self-hosted Apple Silicon
# runner (our real-world dev-machine canary), and on every dev box.
# Skip only the hosted-macOS cell — the production timeout is a real
# code path we still want exercised everywhere else.
_HOSTED_MACOS = (
    sys.platform == "darwin"
    and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
)


def _wait_for_driver(handle, timeout: float) -> int:
    """Wait for the driver to exit; on a timeout, dump its streams."""
    try:
        return handle.proc.wait(timeout=timeout)
    except Exception:
        out, err = _dp.drain_streams(handle.proc)
        pytest.fail(
            f"driver did not exit within {timeout}s\n"
            f"--- driver stdout ---\n{out}\n--- driver stderr ---\n{err}"
        )


def _fail_with_driver_output(handle, message: str) -> None:
    """Fail the test, surfacing driver stdout/stderr (#156 diagnostics)."""
    out, err = _dp.drain_streams(handle.proc)
    pytest.fail(
        f"{message}\nevents={read_events(handle.data_dir)!r}\n"
        f"--- driver stdout ---\n{out}\n--- driver stderr ---\n{err}"
    )


def _event_names(events: list[dict]) -> list[str]:
    return [e["event"] for e in events]


def _spawned(events: list[dict]) -> list[str]:
    return [e["component"] for e in events if e["event"] == "spawn"]


# ---- Scenario 1: manager-only happy path ---------------------------------

@pytest.mark.skipif(
    _HOSTED_MACOS,
    reason=(
        "#156 — hosted GitHub macOS runner is too slow for the 30s "
        "normal-mode cold-boot HEALTH_TIMEOUT on the mock-runtime "
        "cold-start path. Self-hosted Apple Silicon + every dev macOS "
        "passes; this skip is a CI-noise concession only."
    ),
)
@pytest.mark.usefixtures("require_manager_port_free")
def test_manager_handoff_falls_through_to_normal_mode(launcher_driver):
    """Wizard → done → hand-off → normal mode spawns + health-checks runtime."""
    with launcher_driver(
        runtime_mode="health-ok",
        runtime_port=8080,
    ) as handle:
        # Phase 1: drive the wizard's HTTP API.
        session = client.open_session(handle)
        client.pick_topology(handle, session, "manager")

        # Optional sanity: runtime-ready BEFORE done is "booting" because
        # the launcher hasn't spawned the runtime yet.
        booting = client.poll_runtime_ready(
            handle, session, "booting", timeout=2.0,
        )
        assert booting.get("url") is None, booting

        # Verify topology.json was persisted.
        topology_path = handle.data_dir / "topology.json"
        assert topology_path.is_file(), f"missing {topology_path}"

        client.fire_done(handle, session)

        # Phase 2: snapshot the .wizard_done marker quickly, before the
        # launcher's poller observes + deletes it. The launcher polls
        # every 0.5s (WIZARD_DONE_POLL_INTERVAL) so we have a small
        # but real window. If the snapshot misses, that's fine — the
        # final assertions don't require the payload (the launcher
        # already validated it via the same HMAC + secret).
        marker_path = handle.wizard_subdir / wizard_ipc.MARKER_WIZARD_DONE
        snapshot_payload = client.snapshot_marker_quickly(
            marker_path, handle.ipc_secret, handle.data_dir,
        )
        if snapshot_payload is not None:
            assert snapshot_payload["topology"] == "manager", snapshot_payload
            # Issue #170: the marker carries the wizard subprocess'
            # plain-HTTP loopback port (the value pinned via
            # SETHLANS_WIZARD_PORT), NOT the Caddy public TLS port the
            # tray + browser open. Compare against
            # ``wizard_loopback_port`` accordingly.
            assert (
                snapshot_payload["wizard_port"] == handle.wizard_loopback_port
            ), (snapshot_payload, handle.wizard_loopback_port)

        # Phase 3: hand_off_to_runtime applies setup + tears the wizard
        # down, then _run_orchestration falls through to run_normal_mode.
        # Normal mode spawns the mock manager and waits for its health
        # endpoint; once healthy the driver requests quit (standing in
        # for the tray) and the cascade exits 0.
        rc = _wait_for_driver(handle, timeout=60.0)
        if rc != 0:
            _fail_with_driver_output(handle, f"driver exited with rc={rc}")

        # Phase 4: post-handoff side effects.
        events = read_events(handle.data_dir)
        assert {"event": "hand_off", "rc": 0} in events, events
        assert _spawned(events) == ["wizard", "manager"], events
        assert {"event": "cold_boot_ready", "phase": "runtime"} in events, events
        assert "startup_failed" not in _event_names(events), events
        assert topology_path.is_file(), "topology.json missing post-handoff"
        assert (handle.data_dir / ".setup_complete").is_file()
        # The .wizard_done marker MUST be gone (the launcher deletes it
        # inside wait_for_wizard_done before handing off; FR-L13 then
        # best-effort removes the whole wizard dir).
        marker_after = handle.wizard_subdir / wizard_ipc.MARKER_WIZARD_DONE
        assert not marker_after.exists(), (
            f".wizard_done was not deleted by the launcher: {marker_after}"
        )


# ---- Scenario 2: worker-only happy path -----------------------------------

@pytest.mark.skipif(
    _HOSTED_MACOS,
    reason=(
        "#156 — hosted GitHub macOS runner is too slow for the 30s "
        "normal-mode cold-boot HEALTH_TIMEOUT on the mock-runtime "
        "cold-start path (same budget as the manager scenario)."
    ),
)
@pytest.mark.usefixtures("require_worker_port_free")
def test_worker_only_handoff_spawns_worker(launcher_driver):
    """Worker-only topology: normal mode spawns + health-checks the worker.

    Issue #210: the apply pipeline is skipped for ``worker_only``, so the
    launcher itself records ``.setup_complete`` after the hand-off; normal
    mode then normalizes ``worker_only`` and probes the worker on 8081.
    """
    with launcher_driver(
        runtime_mode="health-ok",
        runtime_port=8081,
    ) as handle:
        session = client.open_session(handle)
        client.pick_topology(handle, session, "worker_only")
        client.fire_done(handle, session)

        rc = _wait_for_driver(handle, timeout=60.0)
        if rc != 0:
            _fail_with_driver_output(handle, f"driver exited with rc={rc}")
        events = read_events(handle.data_dir)
        assert {"event": "hand_off", "rc": 0} in events, events
        assert _spawned(events) == ["wizard", "worker"], events
        assert {"event": "cold_boot_ready", "phase": "runtime"} in events, events
        assert "startup_failed" not in _event_names(events), events
        # Written by the launcher (the stubbed apply pipeline never runs
        # for worker_only), so the next launch skips the wizard.
        sentinel = json.loads(
            (handle.data_dir / ".setup_complete").read_text(encoding="utf-8"),
        )
        assert sentinel["topology"] == "worker_only", sentinel
        assert sentinel["completed_at"], sentinel


# ---- Scenario 3: runtime fails after hand-off -----------------------------

@pytest.mark.usefixtures("require_manager_port_free")
def test_runtime_failing_after_handoff_fails_cold_boot(launcher_driver):
    """Mock runtime exits 1 → normal-mode cold boot fails → launcher rc=1.

    Post-#203 contract: ``hand_off_to_runtime`` succeeds (rc=0) without
    spawning anything; ``run_normal_mode`` spawns the runtime and its
    ``_await_cold_boot`` sees the process exit before health, fires
    ``on_startup_failed`` (the splash error card) and returns 1.
    """
    with launcher_driver(
        runtime_mode="fail-immediately",
        runtime_port=8080,
    ) as handle:
        session = client.open_session(handle)
        client.pick_topology(handle, session, "manager")
        client.fire_done(handle, session)

        # wait_for_health notices the dead runtime within one poll
        # interval, so this is fast; the headroom covers the wizard's
        # polite shutdown + Caddy teardown on slow CI runners.
        rc = _wait_for_driver(handle, timeout=60.0)
        if rc != 1:
            _fail_with_driver_output(
                handle, f"driver expected rc=1 (cold-boot failure); got rc={rc}",
            )

        events = read_events(handle.data_dir)
        # The wizard hand-off itself succeeded — the failure is the
        # runtime's, detected by normal mode, not by the hand-off.
        assert {"event": "hand_off", "rc": 0} in events, events
        assert (handle.data_dir / ".setup_complete").is_file()
        assert _spawned(events) == ["wizard", "manager"], events
        failures = [e for e in events if e["event"] == "startup_failed"]
        assert failures == [{
            "event": "startup_failed",
            "reason": orchestration._REASON_MANAGER_TIMEOUT,
            "detail": "",
        }], events
        # The splash must not have been dismissed via the success path
        # for the runtime phase.
        assert {"event": "cold_boot_ready", "phase": "runtime"} not in events
        assert events[-1] == {"event": "exit", "rc": 1}, events


# ---- Scenario 4: wizard exit without handshake ---------------------------

def test_wizard_exit_without_handshake_returns_failure_rc(launcher_driver):
    """SIGTERM the driver before the wizard writes .wizard_done.

    The driver's signal handler (or, on Windows, ``TerminateProcess``)
    ends it before any hand-off; it must not report success and the
    ``.wizard_done`` marker must never have been written.
    """
    with launcher_driver(
        runtime_mode="health-ok",
        runtime_port=8080,
        # idle_timeout is irrelevant for this scenario — we kill the
        # wizard well before it expires.
        idle_timeout=120.0,
    ) as handle:
        # Authenticate but DO NOT pick topology / fire done.
        client.open_session(handle)

        # SIGTERM the driver. On Windows that's TerminateProcess (instant
        # kill); the conftest's _dp.terminate cascades the kill to all
        # descendants via psutil so the wizard / mock don't leak.
        handle.proc.terminate()
        rc = handle.proc.wait(timeout=30.0)

        # Driver should not have returned 0 (no /done/ call was made).
        assert rc != 0, f"driver should have failed; got rc={rc}"
        marker_path = handle.wizard_subdir / wizard_ipc.MARKER_WIZARD_DONE
        assert not marker_path.exists(), (
            f".wizard_done was written despite no /done/ call: {marker_path}"
        )
        assert "hand_off" not in _event_names(read_events(handle.data_dir))
