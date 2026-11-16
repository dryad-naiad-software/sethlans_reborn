# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Manager + restartable worker harness for the worker stop E2E tests.

One manager per class; the worker can be stopped (gracefully through
its web UI control endpoint, or killed) and started again with the
same data directory, so the same identity. The worker's web UI is
enabled on free loopback ports so the test can ask for a graceful
shutdown on every platform (``POST /api/control/shutdown``, the
dashboard's Shutdown button). The worker reports no GPUs
(``SETHLANS_FORCE_CPU_ONLY``) and every job renders on the CPU.

The base class name does not start with ``Test`` so pytest does not
collect it on its own.
"""

import contextlib
import logging
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import requests

from tests.e2e import worker_stop_procs as procs
from tests.e2e.enrollment_helpers import resolve_worker_config_path
from tests.e2e.helpers import admin_login
from tests.e2e.log_capture import peek_log_files
from tests.e2e.process_manager import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    REPO_ROOT,
    build_manager_env,
    build_worker_env,
    find_free_port,
    generate_secrets,
    kill_process_tree,
    manager_session,
    setup_database,
    start_manager,
    start_worker,
    wait_for_manager,
    wait_for_worker,
)

logger = logging.getLogger(__name__)

TEST_SCENE = REPO_ROOT / "tests" / "assets" / "test_scene.blend"
# Default worker UI password (no custom password is set in the test).
UI_PASSWORD = "sethlans"

# One CPU frame of test_scene.blend that takes long enough to be
# interrupted reliably (tens of seconds on a desktop CPU). Adaptive
# sampling and denoising are off so the time does not depend on noise.
SLOW_RENDER_SETTINGS = {
    "cycles.samples": 1024,
    "cycles.use_adaptive_sampling": False,
    "cycles.use_denoising": False,
    "render.resolution_x": 1920,
    "render.resolution_y": 1080,
    "render.resolution_percentage": 100,
}


class JobWatcher:
    """Polls one job and records every status/message change it sees."""

    def __init__(self, session, base_url, job_id):
        self.session = session
        self.url = f"{base_url}/api/jobs/{job_id}/"
        self.history = []  # [(status, error_message)] in order seen
        self.last = {}

    def poll(self):
        resp = self.session.get(self.url, timeout=30)
        resp.raise_for_status()
        self.last = resp.json()
        state = (self.last.get("status"), self.last.get("error_message") or "")
        if not self.history or self.history[-1] != state:
            self.history.append(state)
            logger.info("Job %s -> %s %r", self.last.get("id"), *state)
        return self.last

    def statuses(self):
        return [status for status, _msg in self.history]

    def wait_for(self, predicate, timeout, what, interval=0.5):
        """Poll until ``predicate(job)`` is true; return the job."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.poll()
            if predicate(job):
                return job
            time.sleep(interval)
        raise AssertionError(
            f"Timed out after {timeout}s waiting for {what}; "
            f"statuses seen: {self.history}; last job: {self.last}"
        )


class WorkerStopHarness:
    """Manager + restartable worker shared by the worker stop tests."""

    @classmethod
    def setup_class(cls):
        cls.temp_dir = None
        try:
            cls._start_stack()
        except BaseException:
            cls.teardown_class()
            raise

    @classmethod
    def _start_stack(cls):
        cls.manager_proc = None
        cls.worker_proc = None
        cls.worker_pid = None
        cls.all_worker_procs = []
        cls.session = None
        cls.port = find_free_port()
        cls.base_url = f"https://127.0.0.1:{cls.port}"
        secrets = generate_secrets()
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="e2e_stop_"))
        # Command lines name the temp dir in long or 8.3 short form on
        # Windows; its unique last component matches both.
        cls.worker_scope = f"{cls.temp_dir.name}/worker_data"
        cls.db_path = cls.temp_dir / "test_e2e_db.sqlite3"
        media_root = cls.temp_dir / "media"
        media_root.mkdir(parents=True, exist_ok=True)
        cls.manager_env = build_manager_env(
            cls.db_path, media_root, secrets["enrollment_key"],
            secrets["secret_key"], cls.port,
        )
        worker_data_dir = cls.temp_dir / "worker_data"
        cls.worker_env = build_worker_env(
            secrets["enrollment_key"], "127.0.0.1", cls.port,
            worker_data_dir=worker_data_dir,
        )
        cls.ui_upstream_port = find_free_port()
        ui_port = find_free_port()
        cls.worker_env.update({
            "SETHLANS_WORKER_UI_ENABLED": "true",
            "SETHLANS_WORKER_UI_BIND_ADDRESS": "127.0.0.1",
            "SETHLANS_WORKER_UI_PORT": str(ui_port),
            "SETHLANS_WORKER_CADDY_PUBLIC_TLS_PORT": str(ui_port),
            "SETHLANS_WORKER_CADDY_LOOPBACK_PORT": str(find_free_port()),
            "SETHLANS_WORKER_WAITRESS_UPSTREAM_PORT": str(cls.ui_upstream_port),
            "SETHLANS_FORCE_CPU_ONLY": "true",
        })
        cls.output_dir = resolve_worker_config_path(worker_data_dir).parent / "output"

        setup_database(cls.manager_env)
        cls.manager_proc = start_manager(cls.manager_env, cls.port)
        wait_for_manager(cls.base_url, proc=cls.manager_proc)
        cls.session = manager_session(cls.manager_env)
        admin_login(cls.session, cls.base_url, ADMIN_USERNAME, ADMIN_PASSWORD)
        cls.start_worker_process()
        wait_for_worker(cls.session, cls.base_url, timeout=180)

        resp = cls.session.post(
            f"{cls.base_url}/api/projects/", json={"name": "Worker Stop Project"}, timeout=30,
        )
        assert resp.status_code == 201, resp.text
        project_id = resp.json()["id"]
        with open(TEST_SCENE, "rb") as fh:
            resp = cls.session.post(
                f"{cls.base_url}/api/assets/",
                data={"name": "test_scene.blend", "project": project_id},
                files={"blend_file": ("test_scene.blend", fh, "application/octet-stream")},
                timeout=60,
            )
        assert resp.status_code == 201, resp.text
        cls.asset_id = resp.json()["id"]

    @classmethod
    def teardown_class(cls):
        for proc in cls.all_worker_procs:
            kill_process_tree(proc)
        if cls.manager_proc:
            kill_process_tree(cls.manager_proc)
        # Anything still naming this test's temp dir (an orphaned Blender
        # or Caddy of a killed worker) was started by this test.
        if cls.session is not None:
            cls.session.close()
        if cls.temp_dir is not None:
            procs.sweep(cls.temp_dir.name)
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def teardown_method(self, method):
        """Log the tail of every worker log of this class (failure triage)."""
        for proc in self.all_worker_procs:
            out, err = peek_log_files(proc)
            logger.info("--- WORKER pid %s rc=%s STDOUT (last 6000) ---\n%s",
                        proc.pid, proc.poll(), out[-6000:])
            if err.strip():
                logger.info("--- WORKER pid %s STDERR (last 3000) ---\n%s", proc.pid, err[-3000:])

    # --- worker process control ---

    @classmethod
    def start_worker_process(cls):
        """Start a worker with the class's data directory (same identity)."""
        cls.worker_proc = start_worker(cls.worker_env)
        cls.all_worker_procs.append(cls.worker_proc)
        cls.worker_pid = procs.real_worker_pid(cls.worker_proc)
        logger.info("Worker launcher pid %s, agent pid %s", cls.worker_proc.pid, cls.worker_pid)
        return cls.worker_proc

    def ensure_worker_running(self):
        if self.worker_proc is None or self.worker_proc.poll() is not None:
            self.start_worker_process()

    def request_graceful_stop(self, timeout=60):
        """``POST /api/control/shutdown`` on the worker's web UI (loopback upstream)."""
        url = f"http://127.0.0.1:{self.ui_upstream_port}/api/control/shutdown"
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                resp = requests.post(url, headers={"Authorization": f"Bearer {UI_PASSWORD}"}, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                last = f"HTTP {resp.status_code} {resp.text}"
            except requests.RequestException as exc:
                last = repr(exc)
            time.sleep(1)
        raise AssertionError(f"Worker shutdown request failed: {last}")

    # --- manager API ---

    def submit_job(self, name):
        resp = self.session.post(f"{self.base_url}/api/jobs/", json={
            "name": name,
            "asset_id": self.asset_id,
            "output_file_pattern": f"renders/{name}_####.png",
            "start_frame": 1,
            "end_frame": 1,
            "render_engine": "CYCLES",
            "render_device": "CPU",
            "render_settings": SLOW_RENDER_SETTINGS,
        }, timeout=30)
        assert resp.status_code == 201, f"Job submission failed: {resp.status_code} {resp.text}"
        return resp.json()["id"]

    def set_queue_paused(self, paused):
        action = "pause" if paused else "resume"
        resp = self.session.post(f"{self.base_url}/api/queue-settings/{action}/", timeout=30)
        assert resp.status_code == 200, resp.text

    def requeue_counts(self, job_id):
        """``(auto_requeue_count, yield_requeue_count)``; not exposed by the API."""
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=10)) as conn:
            row = conn.execute(
                "SELECT auto_requeue_count, yield_requeue_count FROM workers_job WHERE id = ?",
                (job_id,),
            ).fetchone()
        assert row is not None, f"Job {job_id} not in the manager database"
        return row

    # --- render observation ---

    def wait_for_render(self, watcher, marker, timeout, cpu_seconds=60.0):
        """Wait until the job is RENDERING and its Blender has done some work.

        Returns the Blender processes rendering it. ``cpu_seconds`` of
        CPU time (summed over threads) means Blender has loaded the
        scene and is sampling.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = watcher.poll()
            assert job["status"] in ("QUEUED", "RENDERING"), f"Unexpected job state: {job}"
            renders = procs.blender_renders(marker)
            if job["status"] == "RENDERING" and renders and max(
                    procs.cpu_seconds(p) for p in renders) >= cpu_seconds:
                return renders
            time.sleep(1)
        raise AssertionError(f"No Blender render of {marker} within {timeout}s; statuses {watcher.history}")
