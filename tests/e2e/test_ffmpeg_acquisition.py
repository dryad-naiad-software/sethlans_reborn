# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
E2E tests for the manager's FFmpeg acquisition paths.

Covers spec ``wizard-ffmpeg-rewrite.md`` AC §489 / §449-450 / FR §27-44:
PATH happy path (system FFmpeg >= 8 -> source=system, status=ready) and
bundled-fallback path (pre-staged binary at <data_dir>/bin/ffmpeg/8.1/
-> source=bundled, version=8.1, status=ready, no network call).

Both tests run against a real Django manager subprocess (Waitress +
Caddy via ``start_manager``); neither needs a worker or Blender.
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import requests

from tests.e2e.helpers import admin_login
from tests.e2e.process_manager import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    build_manager_env,
    find_free_port,
    generate_secrets,
    kill_process_tree,
    setup_database,
    start_manager,
    wait_for_manager,
)

logger = logging.getLogger(__name__)

# Both parts-check branches finish in <1s on a healthy host; 30s is
# a generous regression ceiling.
STATUS_POLL_TIMEOUT = 30
STATUS_POLL_INTERVAL = 0.5

URL_PATH = "/api/ffmpeg-status/"

# Mirrors parts_check.ffmpeg_download_pkg.verify._VERSION_RE.
_VERSION_RE = re.compile(r"^ffmpeg\s+version\s+n?(\d+)\.", re.IGNORECASE)


def _system_ffmpeg_major() -> int:
    """Return the host's system-FFmpeg major version, or 0 if absent."""
    binary = shutil.which("ffmpeg")
    if not binary:
        return 0
    try:
        result = subprocess.run(
            [binary, "-version"],
            capture_output=True, text=True, timeout=10, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.returncode != 0:
        return 0
    first_line = (result.stdout or "").splitlines()[0:1]
    if not first_line:
        return 0
    match = _VERSION_RE.match(first_line[0])
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _poll_admin_status(session, base_url, timeout=STATUS_POLL_TIMEOUT):
    """Poll until ``ffmpeg.status`` becomes terminal (ready/failed).

    Returns ``(payload, observed_states)`` -- distinct successive
    ``ffmpeg.status`` values seen, surfacing the brief ``installing``
    window if caught before the parts-check thread publishes.
    """
    deadline = time.monotonic() + timeout
    observed_states = []
    last_payload = None
    url = f"{base_url}{URL_PATH}"
    while time.monotonic() < deadline:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        # Cache-Control: no-store is a spec invariant.
        assert resp.headers.get("Cache-Control") == "no-store", (
            f"Missing Cache-Control: no-store header on {URL_PATH} "
            f"(got {resp.headers.get('Cache-Control')!r})"
        )
        last_payload = resp.json()
        ffmpeg_block = last_payload.get("ffmpeg") or {}
        state = ffmpeg_block.get("status")
        if state and (not observed_states or observed_states[-1] != state):
            observed_states.append(state)
        if state in ("ready", "failed"):
            return last_payload, tuple(observed_states)
        time.sleep(STATUS_POLL_INTERVAL)
    raise TimeoutError(
        f"FFmpeg status did not reach terminal state in {timeout}s. "
        f"Observed: {observed_states}, last payload: {last_payload}"
    )


def _make_manager_env(temp_dir, port, *, hide_path_ffmpeg=False):
    """Build manager env. ``hide_path_ffmpeg`` empties PATH."""
    secrets = generate_secrets()
    db_path = temp_dir / "test_e2e_db.sqlite3"
    media_root = temp_dir / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    env = build_manager_env(
        db_path, media_root,
        secrets["enrollment_key"], secrets["secret_key"], port,
    )
    if hide_path_ffmpeg:
        empty_path = temp_dir / "empty_path"
        empty_path.mkdir(parents=True, exist_ok=True)
        env["PATH"] = str(empty_path)
    return env, secrets


def _copy_and_verify(source, dest):
    """Copy + smoke-test ``-version``. Catches non-self-contained shims."""
    shutil.copy2(source, dest)
    if platform.system() != "Windows":
        os.chmod(dest, 0o755)
    try:
        result = subprocess.run(
            [str(dest), "-version"],
            capture_output=True, timeout=10, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _stage_bundled_ffmpeg(manager_data_dir):
    """Pre-stage a working FFmpeg at ``<data_dir>/bin/ffmpeg/8.1/``.

    Tries ``which("ffmpeg")`` then well-known Windows install paths
    (chocolatey/scoop ship a shim under PATH and the real binary
    elsewhere). Returns the staged path or ``None``. Reported version
    is hard-coded in the impl to ``FFMPEG_VERSION = "8.1"`` so any
    binary that passes ``verify_runs`` suffices.
    """
    binary_name = (
        "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
    )
    install_dir = manager_data_dir / "bin" / "ffmpeg" / "8.1"
    install_dir.mkdir(parents=True, exist_ok=True)
    dest = install_dir / binary_name

    candidates = []
    primary = shutil.which("ffmpeg")
    if primary:
        candidates.append(primary)
    if platform.system() == "Windows":
        candidates.extend([
            r"C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
        ])
    for src in candidates:
        if src and os.path.isfile(src) and _copy_and_verify(src, dest):
            return dest
    return None


@pytest.fixture
def temp_dir_fixture(tmp_path_factory):
    """Per-test tmp directory; cleaned up by pytest after teardown."""
    yield Path(tmp_path_factory.mktemp("e2e_ffmpeg_acq_"))


class TestFFmpegAcquisition:
    """E2E coverage for the two FFmpeg acquisition paths."""

    def _boot_manager(self, env, port):
        """Spawn manager + Caddy and return ``(proc, session, base_url)``."""
        base_url = f"https://127.0.0.1:{port}"
        setup_database(env)
        proc = start_manager(env, port)
        try:
            wait_for_manager(base_url, proc=proc)
            session = requests.Session()
            session.verify = False
            admin_login(
                session, base_url, ADMIN_USERNAME, ADMIN_PASSWORD,
            )
            return proc, session, base_url
        except Exception:
            kill_process_tree(proc)
            raise

    @pytest.mark.skipif(
        _system_ffmpeg_major() < 8,
        reason=(
            "system ffmpeg < 8.x or absent; PATH happy-path test "
            "requires host FFmpeg >= 8 to exercise the version gate."
        ),
    )
    def test_path_ffmpeg_ready_reports_system_source(
        self, temp_dir_fixture,
    ):
        """PATH happy path: host FFmpeg >= 8 -> source=system, status=ready."""
        port = find_free_port()
        env, _ = _make_manager_env(temp_dir_fixture, port)
        proc, session, base_url = self._boot_manager(env, port)
        try:
            payload, observed = _poll_admin_status(session, base_url)
            ffmpeg = payload.get("ffmpeg")
            assert payload["video_assembly_ready"] is True, payload
            assert ffmpeg is not None, payload
            assert ffmpeg["status"] == "ready", ffmpeg
            assert ffmpeg["source"] == "system", ffmpeg
            assert ffmpeg["error"] is None, ffmpeg
            assert isinstance(ffmpeg["path"], str) and ffmpeg["path"], ffmpeg
            assert "ready" in observed, observed
        finally:
            kill_process_tree(proc)

    def test_bundled_presence_reports_bundled_source(
        self, temp_dir_fixture,
    ):
        """Bundled-fallback path with no real network call.

        Mock boundary: **B** (file-system pre-stage). The manager is a
        subprocess in E2E so in-process monkeypatching is unavailable;
        boundary C (local HTTP fixture) needs a URL-override hook the
        implementation does not expose. Pre-staging at
        ``<data_dir>/bin/ffmpeg/8.1/`` triggers the bundled-presence
        fast path -- the same end-state code path a successful download
        lands on after ``os.replace``. PATH is wiped on the subprocess
        so the parts-check falls past PATH into the bundled-presence
        branch; the download branch is never entered.
        """
        port = find_free_port()
        env, _ = _make_manager_env(
            temp_dir_fixture, port, hide_path_ffmpeg=True,
        )
        # parts_check reads <data_dir>/bin/ffmpeg/8.1/ via
        # shared.frozen_paths.get_data_dir("manager"); the env override
        # SETHLANS_MANAGER_DATA_DIR is set by build_manager_env.
        manager_data_dir = Path(env["SETHLANS_MANAGER_DATA_DIR"])
        staged = _stage_bundled_ffmpeg(manager_data_dir)
        if staged is None:
            pytest.skip(
                "no self-contained ffmpeg on the host; test requires a "
                "working ffmpeg binary that can be copied into the "
                "bundled install location",
            )
        logger.info("Pre-staged bundled FFmpeg at %s", staged)

        proc, session, base_url = self._boot_manager(env, port)
        try:
            payload, observed = _poll_admin_status(session, base_url)
            ffmpeg = payload.get("ffmpeg")
            assert payload["video_assembly_ready"] is True, payload
            assert ffmpeg is not None, payload
            assert ffmpeg["status"] == "ready", ffmpeg
            assert ffmpeg["source"] == "bundled", ffmpeg
            assert ffmpeg["version"] == "8.1", ffmpeg
            assert ffmpeg["error"] is None, ffmpeg
            # ffmpeg.path must resolve inside the staged install dir,
            # not the host's PATH binary.
            staged_resolved = str(staged.resolve())
            ffmpeg_path_resolved = str(Path(ffmpeg["path"]).resolve())
            assert ffmpeg_path_resolved == staged_resolved, (
                f"expected {staged_resolved}; got {ffmpeg_path_resolved}"
            )
            assert "ready" in observed, observed
        finally:
            kill_process_tree(proc)
