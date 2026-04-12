# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Helpers specific to the worker-enrollment E2E tests.

Kept separate from ``tests/e2e/helpers.py`` so the general-purpose
polling/image helpers don't pull TLS/socket code into unrelated test
files, and so the enrollment test module itself stays under the 300
line cap.
"""

import base64
import hashlib
import json
import logging
import platform
import secrets
import shutil
import socket
import ssl
import tempfile
import time
from pathlib import Path

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
)

logger = logging.getLogger(__name__)


def fresh_nonce() -> str:
    """Return a 22-char urlsafe-b64 nonce of 16 random bytes.

    Matches the server-side validator in
    ``workers.serializers_enrollment.EnrollmentRequestSerializer``.
    """
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(16))
        .rstrip(b"=")
        .decode("ascii")
    )


def resolve_worker_config_path(worker_data_dir: Path) -> Path:
    """Return the per-OS path to the worker's ``config.json``.

    Mirrors ``worker.sethlans_worker_agent.config_store.paths.get_data_dir()``
    with the test-specific env-var overrides applied — knowing the path
    lets the tests read the persisted ``cert_fingerprint`` after the
    wizard runs.
    """
    system = platform.system()
    if system == "Windows":
        return worker_data_dir / "Sethlans" / "worker" / "config.json"
    if system == "Darwin":
        return (
            worker_data_dir / "Library" / "Application Support"
            / "Sethlans" / "worker" / "config.json"
        )
    return worker_data_dir / "sethlans" / "worker" / "config.json"


def fetch_live_cert_fingerprint(host: str, port: int) -> str:
    """Connect to ``host:port`` over TLS and return the SHA-256 hex digest.

    The manager serves a self-signed cert so we must disable
    verification — the point of this function is not trust, it's to
    derive the ground-truth fingerprint the worker should have pinned.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    return hashlib.sha256(der).hexdigest()


def wait_for_worker_config(config_path: Path, timeout: float = 60) -> dict:
    """Poll ``config_path`` until the wizard writes a complete triple.

    The wizard writes several keys in sequence; we keep polling until
    ``manager.api_token`` AND ``manager.cert_fingerprint`` are both
    present so we don't race a half-written file.
    """
    deadline = time.monotonic() + timeout
    last_data: dict = {}
    while time.monotonic() < deadline:
        if config_path.exists():
            try:
                last_data = json.loads(config_path.read_text())
            except (OSError, ValueError):
                last_data = {}
            manager_section = last_data.get("manager") or {}
            if (
                manager_section.get("api_token")
                and manager_section.get("cert_fingerprint")
            ):
                return last_data
        time.sleep(1)
    raise TimeoutError(
        f"Worker config at {config_path} did not populate "
        f"within {timeout}s. Last content: {last_data!r}"
    )


class EnrollmentHarness:
    """Shared boot/teardown helper used by the enrollment E2E tests.

    Each test gets a fresh manager + worker + tmp data dir. Keeping
    this as a hand-rolled helper (rather than a pytest fixture) lets
    individual tests customise the worker env — for example, the
    bad-key test substitutes a different
    ``SETHLANS_WORKER_ENROLLMENT_KEY`` without affecting the manager's
    stored key.
    """

    def __init__(self, prefix: str):
        self.temp_dir = Path(
            tempfile.mkdtemp(prefix=f"e2e_enroll_{prefix}_"),
        )
        self.db_path = self.temp_dir / "test_e2e_db.sqlite3"
        self.media_root = self.temp_dir / "media"
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.worker_data_dir = self.temp_dir / "worker_data"
        self.test_secrets = generate_secrets()
        self.port = find_free_port()
        self.base_url = f"https://127.0.0.1:{self.port}"
        self.manager_env = build_manager_env(
            self.db_path, self.media_root,
            self.test_secrets["enrollment_key"],
            self.test_secrets["secret_key"],
            self.port,
        )
        self.manager_proc = None
        self.worker_proc = None
        self.session = None

    def start_manager(self) -> None:
        """Migrate DB, launch manager subprocess, authenticate session."""
        setup_database(self.manager_env)
        self.manager_proc = start_manager(self.manager_env, self.port)
        wait_for_manager(self.base_url, proc=self.manager_proc)
        self.session = requests.Session()
        self.session.verify = False
        admin_login(
            self.session, self.base_url, ADMIN_USERNAME, ADMIN_PASSWORD,
        )

    def start_worker(self, enrollment_key_override: str = None) -> None:
        """Launch the worker subprocess with an isolated data dir."""
        key = (
            enrollment_key_override
            or self.test_secrets["enrollment_key"]
        )
        worker_env = build_worker_env(
            key, "127.0.0.1", self.port,
            worker_data_dir=self.worker_data_dir,
        )
        self.worker_proc = start_worker(worker_env)

    def teardown(self) -> None:
        """Kill both subprocesses and remove the per-test tmp tree."""
        if self.worker_proc:
            kill_process_tree(self.worker_proc)
        if self.manager_proc:
            kill_process_tree(self.manager_proc)
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            logger.exception("Temp dir cleanup failed: %s", self.temp_dir)
