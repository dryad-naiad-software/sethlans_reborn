# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/handlers/ffmpeg.py``
(FR-M2-7 / FR-M2-7a)."""

from __future__ import annotations

import pytest

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers import ffmpeg as ffmpeg_handler

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    ffmpeg_handler.reset_for_tests()
    yield
    auth_state.reset_state_for_tests()
    ffmpeg_handler.reset_for_tests()


@pytest.fixture
def start_handler(tmp_path):
    return ffmpeg_handler.make_start_handler(tmp_path)


@pytest.fixture
def cancel_handler():
    return ffmpeg_handler.make_cancel_handler()


@pytest.fixture
def progress_handler():
    return ffmpeg_handler.make_progress_handler()


class TestStartHandler:

    def test_double_start_returns_same_task_id(
        self, start_handler, monkeypatch, tmp_path,
    ):
        # Stub the worker so the test doesn't try to download.
        from wizard.sethlans_wizard.handlers import ffmpeg as ff_mod
        monkeypatch.setattr(
            ff_mod, "download_worker",
            lambda *args, **kwargs: None,
        )
        # Also short-circuit already_installed so we hit the spawn branch.
        from wizard.sethlans_wizard import ffmpeg_download
        monkeypatch.setattr(
            ffmpeg_download, "already_installed", lambda dd: False,
        )

        env1 = auth_env(b"")
        status1, _, body1 = call_handler(start_handler, env1)
        assert status1.startswith("200"), body1
        first_id = body1["task_id"]

        # Force the registry into an in-progress state by overriding
        # the status that the (now-NOOP) worker thread would have set.
        with ff_mod._task_lock:
            ff_mod._active_task["status"] = "downloading"

        env2 = auth_env(b"")
        status2, _, body2 = call_handler(start_handler, env2)
        assert status2.startswith("200")
        # Single-task invariant: same task_id returned.
        assert body2["task_id"] == first_id


class TestCancelHandler:

    def test_cancel_with_no_active_task_returns_not_found(
        self, cancel_handler,
    ):
        env = auth_env(b"")
        status, _, body = call_handler(cancel_handler, env)
        assert status.startswith("200"), body
        assert body["status"] == "not_found"


class TestProgressHandler:

    def test_unknown_task_id_returns_404(self, progress_handler):
        env = build_environ(
            method="GET",
            path="/api/wizard/ffmpeg/progress/missing-task/",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, _, body = call_handler(progress_handler, env)
        assert status.startswith("404"), body
        assert body["error"] == "unknown_task"

    def test_missing_session_returns_401(self, progress_handler):
        env = build_environ(
            method="GET",
            path="/api/wizard/ffmpeg/progress/x/",
        )
        status, _, _ = call_handler(progress_handler, env)
        assert status.startswith("401")
