# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Invariants of the ``apply_pending_setup`` Django management command.

Covers FR-APPLY3 (post-apply self-check, exit code 2), FR-APPLY-LOG1
(sanitised exception bubbling — no plaintext password in stderr or
caplog), the helper unit-tests (TTL / schema / lock / unlink /
read_pending_setup), and the ``emit_stderr_and_exit`` shim. Gates
live in ``test_apply_pending_setup_gates.py``; lifecycle tests in
``test_apply_pending_setup_lifecycle.py``.
"""

from __future__ import annotations

import logging
import os
import time

import pytest
from django.contrib.auth import get_user_model

from workers.management.commands import (
    apply_pending_setup as apply_cmd,
    apply_pending_setup_helpers as helpers,
)

from ._apply_pending_setup_helpers import (
    STRONG_PASSWORD,
    _ExitCalled,
    _run_command,
    _write_pending,
)


User = get_user_model()


@pytest.fixture(autouse=True)
def _patch_os_exit(monkeypatch):
    """Convert ``os._exit`` into a ``SystemExit`` so pytest can catch it."""
    def fake_exit(code):
        raise _ExitCalled(code)
    monkeypatch.setattr(
        "workers.management.commands.apply_pending_setup_helpers.os._exit",
        fake_exit,
    )


# ---- Self-check (FR-APPLY3) ----------------------------------------------

class TestSelfCheck:

    @pytest.mark.django_db
    def test_self_check_failure_exits_2(self, tmp_path, mocker):
        _write_pending(tmp_path, username="frank")
        # Force the self-check to raise SelfCheckError.
        mocker.patch.object(
            apply_cmd, "post_apply_self_check",
            side_effect=helpers.SelfCheckError("forced"),
        )
        with pytest.raises(SystemExit) as exc:
            _run_command(tmp_path)
        assert exc.value.code == 2


# ---- Sanitised exception bubbling (FR-APPLY-LOG1) ------------------------

class TestSanitisedExceptions:

    @pytest.mark.django_db
    def test_admin_create_failure_does_not_leak_password(
        self, tmp_path, capfd, mocker,
    ):
        _write_pending(
            tmp_path, username="grace", password=STRONG_PASSWORD,
        )
        # Make the create_superuser path raise a generic exception
        # whose message would normally include the password.
        mocker.patch.object(
            User.objects, "create_superuser",
            side_effect=RuntimeError(f"db error near {STRONG_PASSWORD}"),
        )
        with pytest.raises(SystemExit) as exc:
            _run_command(tmp_path)
        assert exc.value.code == 2
        captured = capfd.readouterr()
        # Sanitised stderr must NOT include the password.
        assert STRONG_PASSWORD not in captured.err
        assert STRONG_PASSWORD not in captured.out

    @pytest.mark.django_db
    def test_password_not_in_caplog(self, tmp_path, caplog):
        _write_pending(
            tmp_path, username="harry", password=STRONG_PASSWORD,
        )
        with caplog.at_level(logging.DEBUG):
            _run_command(tmp_path)
        for record in caplog.records:
            assert STRONG_PASSWORD not in record.getMessage()


# ---- Helpers — direct unit tests -----------------------------------------

class TestHelpers:

    def test_is_pending_stale_no_timestamp(self):
        assert helpers.is_pending_stale({}) is True

    def test_is_pending_stale_recent(self):
        assert helpers.is_pending_stale(
            {"created_at_unix": time.time()},
        ) is False

    def test_is_pending_stale_ancient(self):
        assert helpers.is_pending_stale(
            {"created_at_unix": time.time() - (48 * 3600)},
        ) is True

    def test_schema_version_supported(self):
        assert helpers.schema_version_supported(
            {"schema_version": 1},
        ) is True
        assert helpers.schema_version_supported(
            {"schema_version": 99},
        ) is False
        assert helpers.schema_version_supported({}) is False

    def test_acquire_apply_lock_handle(self, tmp_path):
        h = helpers.acquire_apply_lock(tmp_path)
        assert h is not None
        # Holding handle, second attempt must return None.
        h2 = helpers.acquire_apply_lock(tmp_path)
        assert h2 is None
        # Release: close fd so a re-acquire works.
        os.close(h.fd)

    def test_best_effort_unlink_missing_no_raise(self, tmp_path):
        helpers.best_effort_unlink(tmp_path / "ghost")

    def test_read_pending_setup_missing_raises_guard(self, tmp_path):
        with pytest.raises(helpers.PendingSetupGuardError):
            helpers.read_pending_setup(tmp_path / "absent.json")

    def test_read_pending_setup_malformed_raises_guard(self, tmp_path):
        target = tmp_path / "pending_setup.json"
        target.write_text("not json", encoding="utf-8")
        with pytest.raises(helpers.PendingSetupGuardError):
            helpers.read_pending_setup(target)


# ---- Fix 3: in-place payload scrub (Spec 2 security LOW, 6688ada) --------

class TestPayloadScrub:
    """``scrub_payload_password`` mutates the dict so all referrers see None."""

    def test_scrub_clears_password_in_place(self):
        from workers.management.commands.apply_pending_setup_db import (
            scrub_payload_password,
        )
        payload = {
            "admin_user": {
                "username": "alice",
                "password_plaintext": STRONG_PASSWORD,
            },
        }
        # Hold an aliased reference to assert in-place mutation.
        alias = payload
        scrub_payload_password(payload)
        assert alias["admin_user"]["password_plaintext"] is None

    def test_scrub_handles_missing_admin_user(self):
        """Defensive: payload without admin_user must not raise."""
        from workers.management.commands.apply_pending_setup_db import (
            scrub_payload_password,
        )
        scrub_payload_password({})
        scrub_payload_password({"admin_user": None})

    def test_scrub_handles_non_dict(self):
        """Defensive: scrub on a non-dict payload silently no-ops."""
        from workers.management.commands.apply_pending_setup_db import (
            scrub_payload_password,
        )
        scrub_payload_password(None)
        scrub_payload_password("not a dict")


@pytest.mark.django_db
class TestRunPayloadScrubbed:
    """End-to-end: after _run, the payload dict's password is cleared."""

    def test_apply_payload_scrubs_caller_dict(self, tmp_path, mocker):
        """Calling _apply_payload directly: caller's dict is mutated in place.

        The caller (``_run``) keeps a reference to the same dict that
        ``_apply_payload`` receives.  The fix's in-place scrub must
        ensure the caller's view of the dict has the password cleared
        once apply returns.
        """
        from workers.management.commands.apply_pending_setup import Command

        _write_pending(tmp_path, username="ivy", password=STRONG_PASSWORD)
        # Build the payload dict the way _read_and_gate would.
        cmd = Command()
        pending_path = tmp_path / "pending_setup.json"
        progress_path = tmp_path / ".setup_progress.json"
        payload = cmd._read_and_gate(pending_path)
        # Sanity: password is present before apply.
        assert (
            payload["admin_user"]["password_plaintext"] == STRONG_PASSWORD
        )
        cmd._apply_payload(payload, tmp_path, pending_path, progress_path)
        # In-place scrub: caller's dict now has None for the password.
        assert payload["admin_user"]["password_plaintext"] is None

    def test_apply_payload_scrubs_even_on_failure(
        self, tmp_path, mocker,
    ):
        """If apply_atomic raises, the in-place scrub still fires."""
        from workers.management.commands import apply_pending_setup_db
        from workers.management.commands.apply_pending_setup import Command
        from workers.management.commands.apply_pending_setup_helpers import (
            AdminCreateError,
        )

        _write_pending(tmp_path, username="jack", password=STRONG_PASSWORD)
        # Force apply_atomic to raise so we exercise the finally path.
        mocker.patch.object(
            apply_pending_setup_db, "apply_atomic",
            side_effect=AdminCreateError("forced"),
        )
        # Also patch the import binding inside the command module.
        mocker.patch(
            "workers.management.commands.apply_pending_setup.apply_atomic",
            side_effect=AdminCreateError("forced"),
        )
        cmd = Command()
        pending_path = tmp_path / "pending_setup.json"
        progress_path = tmp_path / ".setup_progress.json"
        payload = cmd._read_and_gate(pending_path)
        with pytest.raises(AdminCreateError):
            cmd._apply_payload(payload, tmp_path, pending_path, progress_path)
        assert payload["admin_user"]["password_plaintext"] is None


# ---- emit_stderr_and_exit ------------------------------------------------

class TestEmitStderrAndExit:

    def test_writes_message_and_exits(self, capfd):
        with pytest.raises(_ExitCalled) as exc:
            helpers.emit_stderr_and_exit("hello\n", 2)
        assert exc.value.code == 2
        captured = capfd.readouterr()
        assert "hello" in captured.err

    def test_appends_newline(self, capfd):
        with pytest.raises(_ExitCalled):
            helpers.emit_stderr_and_exit("no-trailing", 1)
        captured = capfd.readouterr()
        assert captured.err.endswith("\n")
