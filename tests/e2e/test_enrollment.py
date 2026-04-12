# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
E2E tests for the new worker-enrollment flow (spec ``worker-enrollment.md``).

The legacy ``X-Enrollment-Key``-on-heartbeat path is gone; workers now
call ``POST /api/enroll/`` once on first run, validate the HMAC-signed
envelope, persist the returned token + cert fingerprint to the per-OS
JSON config store, and subsequently authenticate with the token.

These tests exercise the real flow end-to-end:
- a live manager subprocess seeded with a Crockford key via
  ``SETHLANS_SECURITY_ENROLLMENT_KEY``;
- a live worker subprocess pointed at a per-test tmp data dir whose
  unattended wizard path reads ``SETHLANS_WORKER_ENROLLMENT_KEY``.

Data-dir isolation is critical — see ``env_config.py`` for the
platform-branched ``LOCALAPPDATA`` / ``XDG_DATA_HOME`` / ``HOME``
overrides that keep each test's JSON config store inside a tempdir.

Setup helpers live in ``tests/e2e/enrollment_helpers.py`` so this
module stays under the 300-line cap.
"""

import logging
import time

import pytest
import requests

from tests.e2e.enrollment_helpers import (
    EnrollmentHarness,
    fetch_live_cert_fingerprint,
    fresh_nonce,
    resolve_worker_config_path,
    wait_for_worker_config,
)
from tests.e2e.process_manager import (
    generate_secrets,
    run_management_command,
    wait_for_worker,
)

logger = logging.getLogger(__name__)

# The wizard's unattended backoff schedule is (0, 1, 3, 9, 27); the
# four retry sleeps total ~40 seconds before exit code 4 is returned.
# Leave headroom for process cleanup.
_WORKER_BAD_KEY_WAIT = 75

# Default budget for waiting on the worker's happy-path enrollment.
_WORKER_ENROLL_WAIT = 120


@pytest.fixture
def enrollment_harness():
    """Per-test harness with a fresh manager + worker + tmp data dir.

    Yields before ``start_worker`` / ``start_manager`` so the test can
    tailor the worker env for negative scenarios (bad keys, legacy
    header rejection, key rotation).
    """
    harness = EnrollmentHarness("harness")
    try:
        yield harness
    finally:
        harness.teardown()


class TestEnrollment:
    """Happy-path and regression tests for the new enrollment flow."""

    def test_unattended_wizard_enrolls_and_heartbeats(
        self, enrollment_harness,
    ):
        """Baseline: boot everything, wizard runs, worker appears active."""
        enrollment_harness.start_manager()
        enrollment_harness.start_worker()
        worker_data = wait_for_worker(
            enrollment_harness.session,
            enrollment_harness.base_url,
            timeout=_WORKER_ENROLL_WAIT,
        )
        assert worker_data["is_active"] is True
        assert worker_data.get("hostname"), "Worker has no hostname"
        assert worker_data.get("has_token") is True, (
            "Worker did not receive a token during enrollment"
        )

    def test_enrollment_config_contains_pinned_fingerprint(
        self, enrollment_harness,
    ):
        """FR-21/FR-22: wizard persists the cert fingerprint that matches
        the live manager's actual TLS certificate."""
        enrollment_harness.start_manager()
        live_fp = fetch_live_cert_fingerprint(
            "127.0.0.1", enrollment_harness.port,
        )
        enrollment_harness.start_worker()
        wait_for_worker(
            enrollment_harness.session,
            enrollment_harness.base_url,
            timeout=_WORKER_ENROLL_WAIT,
        )
        config_path = resolve_worker_config_path(
            enrollment_harness.worker_data_dir,
        )
        cfg = wait_for_worker_config(config_path)
        manager_section = cfg.get("manager") or {}
        stored_fp = manager_section.get("cert_fingerprint", "")
        assert stored_fp, "Worker config missing manager.cert_fingerprint"
        assert stored_fp.lower() == live_fp.lower(), (
            f"Worker pinned fingerprint {stored_fp!r} does not match "
            f"manager's live fingerprint {live_fp!r}"
        )
        assert manager_section.get("api_token"), (
            "Worker config missing manager.api_token"
        )
        enrollment_section = cfg.get("enrollment") or {}
        assert enrollment_section.get("wizard_complete") is True, (
            "wizard_complete flag not set after enrollment"
        )

    def test_legacy_heartbeat_enrollment_header_rejected(
        self, enrollment_harness,
    ):
        """AC-21: ``X-Enrollment-Key`` on ``/api/heartbeat/`` is rejected."""
        enrollment_harness.start_manager()
        # No worker needed; the point of this test is to hit the
        # heartbeat endpoint directly without a token.
        resp = requests.post(
            f"{enrollment_harness.base_url}/api/heartbeat/",
            headers={
                "X-Enrollment-Key": (
                    enrollment_harness.test_secrets["enrollment_key"]
                ),
            },
            json={
                "hostname": "rogue_worker",
                "os": "Linux",
                "ip_address": "10.0.0.99",
                "available_tools": {},
            },
            timeout=15,
            verify=False,
        )
        assert resp.status_code == 401, (
            f"Expected 401 for legacy X-Enrollment-Key header, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_rotation_invalidates_new_enrollments_keeps_existing_tokens(
        self, enrollment_harness,
    ):
        """AC-34: rotating the key breaks new enrollments but not
        already-issued tokens."""
        enrollment_harness.start_manager()
        enrollment_harness.start_worker()
        wait_for_worker(
            enrollment_harness.session,
            enrollment_harness.base_url,
            timeout=_WORKER_ENROLL_WAIT,
        )
        # Rotate the enrollment key via the management command.
        result = run_management_command(
            enrollment_harness.manager_env, "rotate_enrollment_key",
        )
        assert result.returncode == 0, (
            f"rotate_enrollment_key failed: {result.stderr}"
        )
        # A fresh enrollment attempt with the OLD key must be rejected.
        # The serializer requires a 22-char urlsafe-b64 nonce that
        # decodes to exactly 16 bytes (see serializers_enrollment.py).
        old_key = enrollment_harness.test_secrets["enrollment_key"]
        probe_nonce = fresh_nonce()
        resp = requests.post(
            f"{enrollment_harness.base_url}/api/enroll/",
            json={
                "enrollment_key": old_key,
                "hostname": "rotation-probe-host",
                "nonce": probe_nonce,
            },
            timeout=15, verify=False,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for rotated-away key, got "
            f"{resp.status_code}: {resp.text}"
        )
        # The already-enrolled worker's token should still authenticate.
        # We assert by confirming the worker is still listed as active
        # after the rotation + heartbeat cycle.
        deadline = time.monotonic() + 30
        still_active = False
        while time.monotonic() < deadline:
            heartbeat_resp = enrollment_harness.session.get(
                f"{enrollment_harness.base_url}/api/heartbeat/",
                timeout=10,
            )
            if heartbeat_resp.status_code == 200:
                active = [
                    w for w in heartbeat_resp.json()
                    if w.get("is_active")
                ]
                if active:
                    still_active = True
                    break
            time.sleep(2)
        assert still_active, (
            "Already-enrolled worker's token stopped working after "
            "the enrollment key was rotated — AC-34 regression."
        )

    def test_invalid_enrollment_key_exits_worker_cleanly(
        self, enrollment_harness,
    ):
        """AC-31a: unattended wizard with a bad key exponentially backs off
        through the schedule (0s/1s/3s/9s/27s) and exits with code 4.

        The backoff totals ~40s; we wait up to 75s to give the cleanup
        path headroom. This is the slow test in the enrollment suite —
        the deterministic coverage of the schedule itself is already
        provided by the unit tests in
        ``tests/unit/worker/test_wizard_unattended.py``.
        """
        enrollment_harness.start_manager()
        # Generate a DIFFERENT valid-format key so the wizard's input
        # validation lets it through to the network call; the manager
        # will reject it at the DB comparison step with a 403.
        bad_key_secrets = generate_secrets()
        bad_key = bad_key_secrets["enrollment_key"]
        assert bad_key != enrollment_harness.test_secrets["enrollment_key"]
        enrollment_harness.start_worker(enrollment_key_override=bad_key)
        deadline = time.monotonic() + _WORKER_BAD_KEY_WAIT
        exit_code = None
        while time.monotonic() < deadline:
            ret = enrollment_harness.worker_proc.poll()
            if ret is not None:
                exit_code = ret
                break
            time.sleep(1)
        assert exit_code is not None, (
            f"Worker did not exit within {_WORKER_BAD_KEY_WAIT}s "
            "after a bad enrollment key"
        )
        # The wizard returns code 4 for unattended failure (see
        # ``worker/sethlans_worker_agent/wizard.py``).
        assert exit_code == 4, (
            f"Expected exit code 4 (WIZARD_UNATTENDED_FAILED) for bad "
            f"key, got {exit_code}"
        )
