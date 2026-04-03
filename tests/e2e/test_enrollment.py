# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
E2E tests for worker enrollment, heartbeat, and auth flow verification.

These tests verify that a worker can enroll with the manager using an
enrollment key, receive a token, and maintain active status via
heartbeats.
"""

import logging
import time

import requests

from tests.e2e.helpers import admin_login
from tests.e2e.process_manager import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    build_manager_env,
    build_worker_env,
    find_free_port,
    generate_secrets,
    kill_process_tree,
    setup_database,
    start_manager,
    start_worker,
    wait_for_manager,
    wait_for_worker,
)

logger = logging.getLogger(__name__)


class TestEnrollment:
    """Tests for worker enrollment and heartbeat."""

    @classmethod
    def setup_class(cls):
        """Start a fresh manager and worker for enrollment tests."""
        cls.manager_proc = None
        cls.worker_proc = None
        cls.manager_stdout = ""
        cls.manager_stderr = ""
        cls.worker_stdout = ""
        cls.worker_stderr = ""

        cls.port = find_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.test_secrets = generate_secrets()

        # Get temp dirs from the e2e_temp_root fixture — but since
        # setup_class runs outside fixture scope, use tempfile directly.
        import tempfile
        from pathlib import Path
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="e2e_enroll_"))
        cls.db_path = cls.temp_dir / "test_e2e_db.sqlite3"
        cls.media_root = cls.temp_dir / "media"
        cls.media_root.mkdir(parents=True, exist_ok=True)

        # Build envs.
        cls.manager_env = build_manager_env(
            cls.db_path, cls.media_root,
            cls.test_secrets["enrollment_key"],
            cls.test_secrets["secret_key"],
            cls.port,
        )
        cls.worker_env = build_worker_env(
            cls.test_secrets["enrollment_key"],
            "127.0.0.1", cls.port,
        )

        # Setup database and start processes.
        setup_database(cls.manager_env)
        cls.manager_proc = start_manager(cls.manager_env, cls.port)
        wait_for_manager(cls.base_url, proc=cls.manager_proc)

        # Create authenticated session.
        cls.session = requests.Session()
        admin_login(cls.session, cls.base_url, ADMIN_USERNAME, ADMIN_PASSWORD)

        # Start worker — it will enroll automatically.
        cls.worker_proc = start_worker(cls.worker_env)

    @classmethod
    def teardown_class(cls):
        """Stop worker and manager, capture logs."""
        if cls.worker_proc:
            cls.worker_stdout, cls.worker_stderr = kill_process_tree(
                cls.worker_proc,
            )
        if cls.manager_proc:
            cls.manager_stdout, cls.manager_stderr = kill_process_tree(
                cls.manager_proc,
            )
        # Clean up temp dir.
        import shutil
        try:
            shutil.rmtree(cls.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def _log_subprocess_output(self):
        """Log captured subprocess output for debugging on failure."""
        if self.manager_stderr:
            logger.info(
                "Manager STDERR:\n%s", self.manager_stderr[-2000:]
            )
        if self.worker_stderr:
            logger.info(
                "Worker STDERR:\n%s", self.worker_stderr[-2000:]
            )

    def test_worker_enrolls_and_appears_active(self):
        """Verify the worker enrolls and shows up in the heartbeat list."""
        worker_data = wait_for_worker(
            self.session, self.base_url, timeout=120,
        )
        assert worker_data["is_active"] is True
        assert worker_data.get("hostname"), "Worker has no hostname"
        assert worker_data.get("id"), "Worker has no ID"

    def test_worker_has_token_after_enrollment(self):
        """After enrollment, the worker should have a token."""
        worker_data = wait_for_worker(
            self.session, self.base_url, timeout=120,
        )
        assert worker_data.get("has_token") is True, (
            "Worker did not receive a token during enrollment"
        )

    def test_heartbeat_list_returns_workers(self):
        """GET /api/heartbeat/ should list at least one worker."""
        # Wait for enrollment first.
        wait_for_worker(self.session, self.base_url, timeout=120)
        resp = self.session.get(
            f"{self.base_url}/api/heartbeat/", timeout=10,
        )
        assert resp.status_code == 200
        workers = resp.json()
        assert len(workers) >= 1, "Expected at least one worker"

    def test_worker_stays_active_across_heartbeats(self):
        """Worker should remain active after multiple heartbeat cycles."""
        wait_for_worker(self.session, self.base_url, timeout=120)
        # Wait for a few heartbeat cycles (interval is 5s in test config).
        time.sleep(12)
        resp = self.session.get(
            f"{self.base_url}/api/heartbeat/", timeout=10,
        )
        assert resp.status_code == 200
        workers = resp.json()
        active = [w for w in workers if w.get("is_active")]
        assert len(active) >= 1, (
            "Worker should still be active after multiple heartbeats"
        )

    def test_enrollment_rejected_without_key(self):
        """A heartbeat with no enrollment key should be rejected."""
        resp = requests.post(
            f"{self.base_url}/api/heartbeat/",
            json={
                "hostname": "rogue_worker",
                "os": "Linux",
                "ip_address": "10.0.0.99",
                "available_tools": {},
            },
            headers={"X-Enrollment-Key": ""},
            timeout=10,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for missing enrollment key, got {resp.status_code}"
        )

    def test_enrollment_rejected_with_wrong_key(self):
        """A heartbeat with an incorrect enrollment key is rejected."""
        resp = requests.post(
            f"{self.base_url}/api/heartbeat/",
            json={
                "hostname": "rogue_worker_2",
                "os": "Linux",
                "ip_address": "10.0.0.100",
                "available_tools": {},
            },
            headers={"X-Enrollment-Key": "completely-wrong-key"},
            timeout=10,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for wrong enrollment key, got {resp.status_code}"
        )
