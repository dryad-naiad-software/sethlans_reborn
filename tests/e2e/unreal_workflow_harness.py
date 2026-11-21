# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Class-scoped manager + Unreal worker harness for the real Unreal E2E
(Unreal foundation FR-T-8).

Boots a manager (Waitress + Caddy) and a worker once per test class the
same way ``core_workflows_harness`` does, then:

- sets the manager's Sethlans share through the admin endpoint
  (``PUT /api/sethlans-share/``, FR-MS-2);
- starts the worker with Unreal enabled, its own share and a dedicated,
  space-free cache dir (FR-WC-2), and waits until it reports the share
  reachable and a GPU memory reading;
- computes the pins with ``manage.py unreal_fingerprint`` (FR-MS-4);
- creates the UNREAL project and the shared_project asset for the PoC.

The share is only read: the PoC archive and overlay already live there.
Constants and the opt-in gating live in ``unreal_e2e_config``.
"""

import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests.e2e import unreal_isolation
from tests.e2e.core_workflows_harness import CoreWorkflowsHarness
from tests.e2e.helpers import POLL_INTERVAL, admin_login
from tests.e2e.unreal_compute_env import compute_env
from tests.e2e.unreal_e2e_config import (
    ARCHIVE_RELPATH,
    ARCHIVE_SHA256_ENV,
    CACHE_DIR_ENV,
    CVAR_OVERRIDES,
    DEFAULT_CACHE_DIR,
    ENGINE_BUILD,
    OVERLAY_RELPATH,
    SHARE,
    UPROJECT_RELPATH,
    archive_on_share,
)
from tests.e2e.process_manager import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    MANAGE_PY,
    build_manager_env,
    build_worker_env,
    find_free_port,
    generate_secrets,
    manager_session,
    setup_database,
    start_manager,
    start_worker,
    wait_for_manager,
    wait_for_worker,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"DONE", "ERROR", "CANCELED"}
_FINGERPRINT_TIMEOUT = 900


def parse_summary(text):
    """``{key: value}`` from an Unreal job summary (``[sethlans-unreal] k=v`` lines)."""
    summary = {}
    for line in (text or "").splitlines():
        body = line.partition("[sethlans-unreal] ")[2]
        key, sep, value = body.partition("=")
        if sep:
            summary[key] = value
    return summary


def fingerprint(manager_env, relpath):
    """Run ``manage.py unreal_fingerprint`` the way an admin would; return the pin."""
    result = subprocess.run(
        [sys.executable, MANAGE_PY, "unreal_fingerprint", relpath],
        env=manager_env, capture_output=True, text=True,
        timeout=_FINGERPRINT_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"unreal_fingerprint {relpath} failed: {result.stderr[-2000:]}"
    )
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.endswith("_sha256"):
            return value.strip()
    raise AssertionError(f"No pin in unreal_fingerprint output: {result.stdout!r}")


def _unreal_ready(worker, share):
    caps = worker.get("unreal_capabilities") or {}
    builds = {i.get("build") for i in caps.get("installs") or [] if i.get("supported")}
    gpus = (worker.get("gpu_memory") or {}).get("gpus") or []
    return (
        worker.get("is_active") and caps.get("enabled") is True
        and ENGINE_BUILD in builds and caps.get("share") == share
        and caps.get("share_reachable") is True and bool(gpus)
    )


def wait_for_unreal_worker(session, base_url, share, timeout=180):
    """Poll the worker list until a worker reports Unreal ready on ``share``."""
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        resp = session.get(f"{base_url}/api/heartbeat/", timeout=30)
        resp.raise_for_status()
        last = resp.json()
        for worker in last:
            if _unreal_ready(worker, share):
                return worker
        time.sleep(POLL_INTERVAL)
    reported = [(w.get("unreal_capabilities"), w.get("gpu_memory")) for w in last]
    raise TimeoutError(
        f"No worker reported Unreal ready on {share} within {timeout}s: {reported}"
    )


def _get(session, url):
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def poll_sampling(session, url, timeout, sample, held_url=None, terminal=_TERMINAL_STATUSES):
    """``poll_for_completion`` that calls ``sample()`` on every round.

    With ``held_url``, that job must stay QUEUED until ``url`` is terminal.
    It is read before ``url``, so a held job seen outside QUEUED while the
    later read is still not terminal was released too early. ``terminal``
    is the set of final statuses (a sequence version also ends PARTLY_DONE).
    """
    deadline = time.monotonic() + timeout
    data = {}
    while time.monotonic() < deadline:
        sample()
        held = _get(session, held_url) if held_url else None
        data = _get(session, url)
        if data["status"] in terminal:
            return data
        assert held is None or held["status"] == "QUEUED", (
            f"Held job left QUEUED ({held['status']}) while {url} was {data['status']}"
        )
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"{url} not terminal within {timeout}s (last status: {data.get('status')})"
    )


class UnrealWorkflowHarness(CoreWorkflowsHarness):
    """Manager + Unreal worker lifecycle; teardown and log dumps are inherited."""

    @classmethod
    def setup_class(cls):
        """Start manager and worker, set the share, create project and asset."""
        cls.manager_proc = None
        cls.worker_proc = None
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="e2e_unreal_"))
        try:
            cls._boot()
        except BaseException:
            # pytest skips teardown_class when setup_class raises.
            cls.teardown_class()
            raise

    @classmethod
    def _boot(cls):
        # A worker without a card choice is CPU-only and never claims an
        # Unreal job; stop now rather than at the claim timeout.
        cls.compute_env, skip_reason = compute_env()
        if skip_reason:
            pytest.skip(f"Unreal E2E needs a GPU card choice: {skip_reason}")
        running = unreal_isolation.running_unreal_processes()
        assert not running, (
            f"Unreal is already running (an artist's editor or a leftover); "
            f"close it before this test: {running}"
        )
        cls.cache_dir = os.environ.get(CACHE_DIR_ENV) or DEFAULT_CACHE_DIR
        assert " " not in cls.cache_dir, f"{CACHE_DIR_ENV} must not contain spaces"
        cls.isolation_before = unreal_isolation.take_snapshot(
            cls.temp_dir / "hkcu_before.reg", archive_on_share(),
        )
        cls._start_manager()
        cls.share = cls._set_manager_share()
        cls._start_unreal_worker()
        cls.archive_sha256 = (
            os.environ.get(ARCHIVE_SHA256_ENV)
            or fingerprint(cls.manager_env, ARCHIVE_RELPATH)
        )
        cls.overlay_sha256 = fingerprint(cls.manager_env, OVERLAY_RELPATH)
        cls.project_id = cls._create_project()
        cls.asset_id = cls._create_asset()

    @classmethod
    def _start_manager(cls):
        cls.port = find_free_port()
        cls.base_url = f"https://127.0.0.1:{cls.port}"
        cls.test_secrets = generate_secrets()
        media_root = cls.temp_dir / "media"
        media_root.mkdir(parents=True, exist_ok=True)
        cls.manager_env = build_manager_env(
            cls.temp_dir / "test_e2e_db.sqlite3", media_root,
            cls.test_secrets["enrollment_key"],
            cls.test_secrets["secret_key"],
            cls.port,
        )
        setup_database(cls.manager_env)
        cls.manager_proc = start_manager(cls.manager_env, cls.port)
        wait_for_manager(cls.base_url, proc=cls.manager_proc)
        cls.session = manager_session(cls.manager_env)
        admin_login(cls.session, cls.base_url, ADMIN_USERNAME, ADMIN_PASSWORD)

    @classmethod
    def _set_manager_share(cls):
        resp = cls.session.put(
            f"{cls.base_url}/api/sethlans-share/",
            json={"sethlans_share": SHARE}, timeout=30,
        )
        assert resp.status_code == 200, (
            f"Setting the share failed: {resp.status_code} {resp.text}"
        )
        return resp.json()["sethlans_share"]

    @classmethod
    def _start_unreal_worker(cls):
        worker_env = build_worker_env(
            cls.test_secrets["enrollment_key"], "127.0.0.1", cls.port,
            worker_data_dir=cls.temp_dir / "worker_data",
        )
        worker_env.update({
            "SETHLANS_UNREAL_ENABLED": "true",
            "SETHLANS_UNREAL_SHARE": SHARE,
            "SETHLANS_UNREAL_CACHE_DIR": cls.cache_dir,
            **cls.compute_env,
        })
        cls.worker_proc = start_worker(worker_env)
        wait_for_worker(cls.session, cls.base_url, timeout=180)
        cls.worker_data = wait_for_unreal_worker(
            cls.session, cls.base_url, cls.share,
        )

    @classmethod
    def _create_project(cls):
        resp = cls.session.post(
            f"{cls.base_url}/api/projects/",
            json={
                "name": "E2E Unreal PoC",
                "renderer": "UNREAL",
                "engine_build": ENGINE_BUILD,
                "unreal_cvar_overrides": CVAR_OVERRIDES,
            },
            timeout=30,
        )
        assert resp.status_code == 201, (
            f"Project creation failed: {resp.status_code} {resp.text}"
        )
        return resp.json()["id"]

    @classmethod
    def _create_asset(cls):
        fields = {
            "name": "ACOMAnimationSample",
            "project": str(cls.project_id),
            "source_type": "shared_project",
            "archive_path": ARCHIVE_RELPATH,
            "uproject_path": UPROJECT_RELPATH,
            "archive_sha256": cls.archive_sha256,
            "overlay_path": OVERLAY_RELPATH,
            "overlay_sha256": cls.overlay_sha256,
        }
        # The asset endpoint parses multipart only; send the fields as parts.
        resp = cls.session.post(
            f"{cls.base_url}/api/assets/",
            files={key: (None, value) for key, value in fields.items()},
            timeout=30,
        )
        assert resp.status_code == 201, (
            f"Asset creation failed: {resp.status_code} {resp.text}"
        )
        return resp.json()["id"]
