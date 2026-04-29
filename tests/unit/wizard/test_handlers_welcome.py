# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/welcome.py`` (FR-M2-1).

Smoke + representative error paths:
* method gate (only POST),
* session header gate (401 without valid X-Wizard-Session),
* query-string forbidden-key gate,
* happy path writes the ``welcome_seen`` checkpoint,
* idempotent re-submission is a no-op.
"""

from __future__ import annotations

from wizard.sethlans_wizard import auth_state, progress
from wizard.sethlans_wizard.checkpoints import WELCOME_SEEN
from wizard.sethlans_wizard.handlers import welcome as welcome_handler

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


def _reset(monkeypatch):
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)


def test_get_returns_405(tmp_path, monkeypatch):
    _reset(monkeypatch)
    handler = welcome_handler.make_welcome_handler(tmp_path)
    env = build_environ(
        method="GET",
        path="/api/wizard/welcome/",
        headers={"X-Wizard-Session": VALID_SESSION},
    )
    status, headers, _ = call_handler(handler, env)
    assert status.startswith("405")
    assert headers.get("Allow") == "POST"


def test_missing_session_returns_401(tmp_path, monkeypatch):
    _reset(monkeypatch)
    handler = welcome_handler.make_welcome_handler(tmp_path)
    env = build_environ(method="POST", path="/api/wizard/welcome/")
    status, _, body = call_handler(handler, env)
    assert status.startswith("401"), body


def test_query_string_token_rejected(tmp_path, monkeypatch):
    _reset(monkeypatch)
    handler = welcome_handler.make_welcome_handler(tmp_path)
    env = build_environ(
        method="POST",
        path="/api/wizard/welcome/",
        query_string="session_token=foo",
        headers={"X-Wizard-Session": VALID_SESSION},
    )
    status, _, _ = call_handler(handler, env)
    assert status.startswith("400")


def test_happy_path_writes_checkpoint(tmp_path, monkeypatch):
    _reset(monkeypatch)
    handler = welcome_handler.make_welcome_handler(tmp_path)
    env = auth_env(b"", method="POST", path="/api/wizard/welcome/")
    status, _, body = call_handler(handler, env)
    assert status.startswith("200"), body
    assert body == {"status": "ok"}
    payload = progress.read_checkpoints(tmp_path)
    assert payload.get("checkpoints") == [WELCOME_SEEN]


def test_repeat_submission_is_idempotent(tmp_path, monkeypatch):
    _reset(monkeypatch)
    handler = welcome_handler.make_welcome_handler(tmp_path)
    env1 = auth_env(b"", method="POST", path="/api/wizard/welcome/")
    call_handler(handler, env1)
    env2 = auth_env(b"", method="POST", path="/api/wizard/welcome/")
    status, _, _ = call_handler(handler, env2)
    assert status.startswith("200")
    payload = progress.read_checkpoints(tmp_path)
    # Append is idempotent — name appears exactly once.
    assert payload.get("checkpoints") == [WELCOME_SEEN]
