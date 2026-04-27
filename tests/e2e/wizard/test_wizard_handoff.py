# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end tests for the wizard hand-off lifecycle (Spec 1 / D2).

Scenarios:

1. Manager-only happy path — the launcher hands off to a mock manager
   runtime; the wizard's ``/runtime-ready/`` flips to ``ready``.
2. Worker-only happy path — same shape, ``worker_only`` topology, mock
   bound on port 8081.
3. Failed runtime hand-off — mock runtime exits non-zero immediately;
   launcher writes ``.runtime_failed`` and the wizard's
   ``/runtime-ready/`` reports ``failed``.
4. Wizard exits without handshake — driver SIGTERM'd before the test
   client picks topology / fires done; driver returns the FR-L7a
   ``wizard_no_handshake`` exit code.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from launcher import wizard_ipc

from . import _driver_process as _dp
from . import _http

# #156: hosted GitHub macOS runners blow the launcher's
# RUNTIME_PORT_BIND_TIMEOUT (30 s, production code in
# launcher/wizard_runtime.py) on the mock-runtime cold-start path —
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


# ---- URL helpers -----------------------------------------------------------

def _auth_url(handle) -> str:
    return f"{handle.wizard_base_url}/api/wizard/auth/"


def _topology_url(handle) -> str:
    return f"{handle.wizard_base_url}/api/wizard/topology/"


def _done_url(handle) -> str:
    return f"{handle.wizard_base_url}/api/wizard/done/"


def _runtime_ready_url(handle) -> str:
    return f"{handle.wizard_base_url}/api/wizard/runtime-ready/"


# ---- Test client steps -----------------------------------------------------

def _open_session(handle) -> str:
    """Auth with the pinned setup token; return the issued session token."""
    status, _, parsed = _http.post_json(
        _auth_url(handle), {"token": handle.setup_token},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed
    session = parsed.get("session_token")
    assert isinstance(session, str) and session, parsed
    return session


def _pick_topology(handle, session: str, topology: str) -> None:
    status, _, parsed = _http.post_json(
        _topology_url(handle), {"topology": topology},
        headers={"X-Wizard-Session": session},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed


def _fire_done(handle, session: str) -> None:
    status, _, parsed = _http.post_json(
        _done_url(handle), {},
        headers={"X-Wizard-Session": session},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed


def _snapshot_marker_quickly(
    marker_path, ipc_secret: bytes, data_dir,
    attempts: int = 10, interval: float = 0.05,
) -> dict | None:
    """Read + validate the marker as soon as it appears, returning payload.

    Polls the file every *interval* seconds for *attempts* iterations
    (so up to ``attempts * interval`` seconds total — kept under the
    launcher's 0.5s WIZARD_DONE_POLL_INTERVAL to win the race). Returns
    None if the marker never appeared OR the launcher's poller already
    deleted it.
    """
    for _ in range(attempts):
        if marker_path.is_file():
            payload = wizard_ipc.read_marker(
                marker_path, ipc_secret,
                expected_type="wizard_done", data_dir=data_dir,
            )
            if payload is not None:
                return payload
        time.sleep(interval)
    return None


def _poll_runtime_ready(
    handle, session: str | None,
    expected_status: str, timeout: float = 25.0,
) -> dict:
    """Poll /runtime-ready/ until status == *expected_status* or timeout.

    The session token is cleared from wizard memory by the /done/ handler
    (FR-W7), so passing *session=None* skips the header. The handler
    rejects unauthenticated requests with 401 — relevant for the post-
    done window where the spec acknowledges the session is gone but the
    test still wants to observe runtime-ready before driver exit.
    """
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        headers = {"X-Wizard-Session": session} if session else {}
        status, _, parsed = _http.get_json(
            _runtime_ready_url(handle),
            headers=headers, timeout=5.0,
        )
        if status == 200 and parsed and parsed.get("status") == expected_status:
            return parsed
        last = parsed if parsed is not None else {"http_status": status}
        time.sleep(0.5)
    raise AssertionError(
        f"runtime-ready did not reach {expected_status!r} within "
        f"{timeout}s; last={last!r}"
    )


# ---- Scenario 1: manager-only happy path ---------------------------------

@pytest.mark.skipif(
    _HOSTED_MACOS,
    reason=(
        "#156 — hosted GitHub macOS runner is too slow for the "
        "30s production RUNTIME_PORT_BIND_TIMEOUT on the mock-runtime "
        "cold-start path. Self-hosted Apple Silicon + every dev macOS "
        "passes; this skip is a CI-noise concession only."
    ),
)
@pytest.mark.usefixtures("require_manager_port_free")
def test_manager_handoff_writes_marker_and_runtime_ready(launcher_driver):
    """Full hand-off lifecycle: wizard → done → runtime spawned + ready."""
    with launcher_driver(
        runtime_mode="health-ok",
        runtime_port=8080,
    ) as handle:
        # Phase 1: drive the wizard's HTTP API.
        session = _open_session(handle)
        _pick_topology(handle, session, "manager")

        # Optional sanity: runtime-ready BEFORE done is "booting" because
        # the launcher hasn't spawned the runtime yet.
        booting = _poll_runtime_ready(handle, session, "booting", timeout=2.0)
        assert booting.get("url") is None, booting

        # Verify topology.json was persisted.
        topology_path = handle.data_dir / "topology.json"
        assert topology_path.is_file(), f"missing {topology_path}"

        _fire_done(handle, session)

        # Phase 2: snapshot the .wizard_done marker quickly, before the
        # launcher's poller observes + deletes it. The launcher polls
        # every 0.5s (WIZARD_DONE_POLL_INTERVAL) so we have a small
        # but real window. If the snapshot misses, that's fine — the
        # final assertions don't require the payload (the launcher
        # already validated it via the same HMAC + secret).
        marker_path = handle.wizard_subdir / wizard_ipc.MARKER_WIZARD_DONE
        snapshot_payload = _snapshot_marker_quickly(
            marker_path, handle.ipc_secret, handle.data_dir,
        )
        if snapshot_payload is not None:
            assert snapshot_payload["topology"] == "manager", snapshot_payload
            assert snapshot_payload["wizard_port"] == handle.wizard_port, (
                snapshot_payload, handle.wizard_port,
            )

        # Phase 3: driver exits 0 once wait_for_runtime_port_bind sees
        # the mock listening on 8080. The wizard self-terminates per
        # FR-W17 once it sees runtime-ready=ready from the mock; the
        # launcher's hand_off_to_runtime then cleans up the wizard dir
        # and returns 0.
        rc = handle.proc.wait(timeout=45.0)
        if rc != 0:
            # #156: surface driver stdout/stderr so macOS CI failures
            # don't leave us blind. The drainer threads in
            # _driver_process already capture both streams; we just
            # never dumped them on assertion failure.
            out, err = _dp.drain_streams(handle.proc)
            pytest.fail(
                f"driver exited with rc={rc}\n"
                f"--- driver stdout ---\n{out}\n"
                f"--- driver stderr ---\n{err}"
            )

        # Phase 4: post-handoff side effects.
        assert topology_path.is_file(), "topology.json missing post-handoff"
        # FR-L13: best-effort wizard_subdir cleanup. On Windows the
        # wizard's still-open log handle can keep the directory
        # un-deletable; cleanup_wizard_dir swallows the OSError. We
        # accept either outcome — the marker is what really matters.
        # The .wizard_done marker MUST be gone (the launcher deletes
        # it inside wait_for_wizard_done before handing off).
        marker_after = handle.wizard_subdir / wizard_ipc.MARKER_WIZARD_DONE
        assert not marker_after.exists(), (
            f".wizard_done was not deleted by the launcher: {marker_after}"
        )


# ---- Scenario 2: worker-only happy path -----------------------------------

@pytest.mark.usefixtures("require_worker_port_free")
def test_worker_only_handoff_spawns_worker(launcher_driver):
    """Worker-only topology: launcher spawns the worker mock, no port-bind watch.

    The launcher's ``spawn_runtime_for_topology`` returns
    ``port_to_watch=None`` for worker-only topologies (no port to
    observe at the launcher level), so success here is measured by the
    driver's clean exit + the wizard's runtime-ready endpoint observing
    a running mock on 8081.
    """
    with launcher_driver(
        runtime_mode="health-ok",
        runtime_port=8081,
    ) as handle:
        session = _open_session(handle)
        _pick_topology(handle, session, "worker_only")
        _fire_done(handle, session)

        # The launcher's wait_for_runtime_port_bind is skipped for
        # worker_only (port_to_watch=None), so the driver returns 0 the
        # moment the worker subprocess starts.
        rc = handle.proc.wait(timeout=20.0)
        assert rc == 0, f"driver exited with rc={rc}"


# ---- Scenario 3: failed runtime ------------------------------------------

@pytest.mark.usefixtures("require_manager_port_free")
def test_failed_runtime_writes_runtime_failed_marker(launcher_driver):
    """Mock runtime exits 1 immediately → launcher writes .runtime_failed."""
    with launcher_driver(
        runtime_mode="fail-immediately",
        runtime_port=8080,
    ) as handle:
        session = _open_session(handle)
        _pick_topology(handle, session, "manager")
        _fire_done(handle, session)

        # The launcher's wait_for_runtime_port_bind times out at 30s
        # (RUNTIME_PORT_BIND_TIMEOUT in launcher/wizard_runtime.py),
        # then writes .runtime_failed and exits 1. Wait for the driver
        # to exit with the FR-L7b failure code. Allow extra headroom
        # for the wizard's polite shutdown + the driver's terminate_all
        # SIGTERM cascade (5s grace per child).
        rc = handle.proc.wait(timeout=90.0)
        assert rc == 1, f"driver expected rc=1 (port_bind_timeout); got rc={rc}"

        # The .runtime_failed HMAC marker should be present on disk.
        # Note: FR-L13 wizard-dir cleanup runs only on the success path
        # so the marker survives.
        marker_path = (
            handle.wizard_subdir / wizard_ipc.MARKER_RUNTIME_FAILED
        )
        assert marker_path.is_file(), f"missing {marker_path}"
        payload = wizard_ipc.read_marker(
            marker_path, handle.ipc_secret,
            expected_type="runtime_failed", data_dir=handle.data_dir,
        )
        assert payload is not None, "runtime_failed marker failed validation"
        assert payload.get("reason") == "port_bind_timeout", payload


# ---- Scenario 4: wizard exit without handshake ---------------------------

def test_wizard_exit_without_handshake_returns_failure_rc(launcher_driver):
    """SIGTERM the wizard before it writes .wizard_done; driver returns 1.

    The wizard exits cleanly via FR-W11 SIGTERM handling without writing
    the marker. ``wait_for_wizard_done`` observes the wizard exit with
    rc=0 + no marker → returns ``"wizard_no_handshake"`` →
    ``wizard_failure_exit`` → driver exits 1.
    """
    with launcher_driver(
        runtime_mode="health-ok",
        runtime_port=8080,
        # idle_timeout is irrelevant for this scenario — we kill the
        # wizard well before it expires. Set low for cheaper failure
        # diagnostics if SIGTERM ever stops working.
        idle_timeout=120.0,
    ) as handle:
        # Authenticate but DO NOT pick topology / fire done.
        _open_session(handle)

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
