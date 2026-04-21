# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``handle_start_blender_download`` (sync WSGI).

Covers happy path, in-progress/already-installed guards, default
version fallback, body validation, and contention.  Progress /
cancel tests live in ``test_setup_handlers_blender.py``.  All
tests mock ``download_blender_with_progress`` so no real download
is ever spawned.
"""

import io
import json
import threading

from sethlans_worker_agent.web_ui.setup.handlers_blender import (
    handle_start_blender_download,
)
from sethlans_worker_agent.web_ui.setup import download_progress
from sethlans_worker_agent.web_ui.setup import handlers_status
from sethlans_worker_agent.web_ui.setup import lock as lock_module

from tests.unit.worker._wsgi_helpers import StartResponseCapture


_PATH = '/api/setup/blender/start-download/'


def _environ_post(body: bytes, include_length: bool = True) -> dict:
    env = {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': _PATH,
        'wsgi.input': io.BytesIO(body),
    }
    if include_length:
        env['CONTENT_LENGTH'] = str(len(body))
    return env


def _status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])


def _call(body: bytes, cap: StartResponseCapture) -> bytes:
    return b''.join(
        handle_start_blender_download(_environ_post(body), cap),
    )


def _patch_default_fresh_install(mocker):
    """Mock tool_manager so the "already installed" guard is skipped."""
    mocker.patch(
        "sethlans_worker_agent.tool_manager.tool_manager_instance"
        "._resolve_version",
        return_value="",
    )
    mocker.patch(
        "sethlans_worker_agent.tool_manager.tool_manager_instance"
        ".get_blender_executable_path",
        return_value=None,
    )


def _patch_download_to_noop(mocker):
    """Replace the background download worker with a no-op."""
    return mocker.patch(
        "sethlans_worker_agent.web_ui.setup.blender_download"
        ".download_blender_with_progress",
        autospec=True,
        return_value=None,
    )


class TestStartBlenderDownloadHappyPath:
    def test_starts_new_download_with_explicit_version(self, mocker):
        _patch_default_fresh_install(mocker)
        mock_dl = _patch_download_to_noop(mocker)

        body = json.dumps({"version": "4.2.1"}).encode()
        cap = StartResponseCapture()
        out = _call(body, cap)

        assert _status_code(cap) == 200
        resp = json.loads(out)
        assert resp["status"] == "started"
        task_id = resp["task_id"]
        assert task_id.startswith("blender_")

        # Registry reflects the new task.
        assert download_progress.get_task(task_id) is not None

        # Background thread joined (it's a no-op); confirm the
        # handler passed the task_id and version through.
        for t in threading.enumerate():
            if t.name.startswith("blender-download-"):
                t.join(timeout=5)
        assert mock_dl.called
        args, _kwargs = mock_dl.call_args
        assert args == (task_id, "4.2.1")

    def test_default_version_fallback(self, mocker):
        """Empty/missing version -> handler uses DEFAULT (4.2)."""
        _patch_default_fresh_install(mocker)
        mock_dl = _patch_download_to_noop(mocker)

        body = json.dumps({}).encode()
        cap = StartResponseCapture()
        out = _call(body, cap)

        assert _status_code(cap) == 200
        resp = json.loads(out)
        assert resp["status"] == "started"

        for t in threading.enumerate():
            if t.name.startswith("blender-download-"):
                t.join(timeout=5)
        assert mock_dl.called
        args, _kwargs = mock_dl.call_args
        # Default fallback is '4.2' per the handler.
        assert args[1] == "4.2"

    def test_thread_name_includes_task_id_prefix(self, mocker):
        """Spawned thread name must start with ``blender-download-``."""
        _patch_default_fresh_install(mocker)

        gate = threading.Event()

        def blocker(task_id, version):
            # Keep the thread alive long enough for the test to
            # inspect ``threading.enumerate()``.
            gate.wait(timeout=5)

        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.blender_download"
            ".download_blender_with_progress",
            side_effect=blocker,
        )

        body = json.dumps({"version": "4.2.1"}).encode()
        cap = StartResponseCapture()
        out = _call(body, cap)

        try:
            assert _status_code(cap) == 200
            task_id = json.loads(out)["task_id"]
            expected_prefix = f"blender-download-{task_id[:8]}"
            matches = [
                t for t in threading.enumerate()
                if t.name == expected_prefix
            ]
            assert matches, (
                "spawned thread with expected name not found: "
                f"{[t.name for t in threading.enumerate()]}"
            )
        finally:
            gate.set()
            for t in threading.enumerate():
                if t.name.startswith("blender-download-"):
                    t.join(timeout=5)


class TestStartBlenderDownloadGuards:
    def test_already_in_progress_returns_existing_task(self, mocker):
        # Seed an in-progress task; handler should report it.
        existing_id, _ = download_progress.create_tagged_task("blender_")
        download_progress.update_task(
            existing_id, status="downloading", percent=17,
        )

        _patch_default_fresh_install(mocker)
        mock_dl = _patch_download_to_noop(mocker)

        body = json.dumps({"version": "4.2.1"}).encode()
        cap = StartResponseCapture()
        out = _call(body, cap)

        assert _status_code(cap) == 200
        resp = json.loads(out)
        assert resp["task_id"] == existing_id
        assert resp["status"] == "downloading"
        assert resp["percent"] == 17
        assert "Download already in progress" in resp["message"]

        # No new download thread spawned.
        assert not mock_dl.called

    def test_already_installed_appends_checkpoint(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.tool_manager.tool_manager_instance"
            "._resolve_version",
            return_value="4.2.1",
        )
        mocker.patch(
            "sethlans_worker_agent.tool_manager.tool_manager_instance"
            ".get_blender_executable_path",
            return_value="/fake/blender/4.2.1/blender",
        )
        mock_dl = _patch_download_to_noop(mocker)

        body = json.dumps({"version": "4.2.1"}).encode()
        cap = StartResponseCapture()
        out = _call(body, cap)

        assert _status_code(cap) == 200
        resp = json.loads(out)
        assert resp == {
            "status": "already_installed",
            "version": "4.2.1",
        }
        assert "blender_installed" in handlers_status.get_current_checkpoints()

        # No download spawned.
        assert not mock_dl.called


class TestStartBlenderDownloadValidation:
    def test_malformed_json_returns_400(self, mocker):
        _patch_default_fresh_install(mocker)
        _patch_download_to_noop(mocker)

        cap = StartResponseCapture()
        out = _call(b'not-json{', cap)

        assert _status_code(cap) == 400
        assert json.loads(out) == {"error": "Invalid JSON"}

    def test_non_object_body_returns_400(self, mocker):
        _patch_default_fresh_install(mocker)
        _patch_download_to_noop(mocker)

        body = json.dumps([1, 2]).encode()
        cap = StartResponseCapture()
        out = _call(body, cap)

        assert _status_code(cap) == 400
        assert "JSON object" in json.loads(out)["error"]

    def test_body_over_cap_returns_413(self, mocker):
        _patch_default_fresh_install(mocker)
        _patch_download_to_noop(mocker)

        body = b'x' * (4096 + 1)
        cap = StartResponseCapture()
        out = _call(body, cap)

        assert _status_code(cap) == 413
        assert json.loads(out) == {"error": "Request body too large"}


class TestStartBlenderDownloadContention:
    def test_contention_returns_409(self, mocker):
        """Holding the mutation lock elsewhere causes a 409."""
        _patch_default_fresh_install(mocker)
        mock_dl = _patch_download_to_noop(mocker)

        lock_held = threading.Event()
        release = threading.Event()

        def holder():
            with lock_module.setup_mutation_lock() as acquired:
                assert acquired
                lock_held.set()
                release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert lock_held.wait(timeout=5)

            body = json.dumps({"version": "4.2.1"}).encode()
            cap = StartResponseCapture()
            out = _call(body, cap)

            assert _status_code(cap) == 409
            assert json.loads(out) == {
                "error": "Setup mutation in progress; retry after "
                         "current operation completes.",
            }
            # The handler must not have spawned a download.
            assert not mock_dl.called
        finally:
            release.set()
            t.join(timeout=5)
