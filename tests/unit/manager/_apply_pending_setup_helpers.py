# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers for the ``apply_pending_setup`` command test splits.

Extracted so the gates / invariants / lifecycle test files can stay
under the 300-line cap without duplicating the pending-setup writer
or the ``os._exit`` shim.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from workers.management.commands import (
    apply_pending_setup_helpers as helpers,
)


# A password that easily satisfies Django's default validators.
STRONG_PASSWORD = "Tr0pical!Mongoose-Hops42"


class _ExitCalled(SystemExit):
    """Replacement for ``os._exit`` so tests can observe the exit code."""


def _write_pending(
    data_dir: Path,
    *,
    schema_version: int = 1,
    topology: str = "manager",
    auto_enroll: bool = False,
    created_at: float | None = None,
    username: str = "admin1",
    email: str = "admin@example.com",
    password: str = STRONG_PASSWORD,
) -> Path:
    """Write a pending_setup.json fixture file to *data_dir*."""
    if created_at is None:
        created_at = time.time()
    payload = {
        "schema_version": schema_version,
        "topology": topology,
        "created_at_unix": created_at,
        "admin_user": {
            "username": username,
            "email": email,
            "password_plaintext": password,
        },
        "worker_ui_password_hash": None,
        "worker_ui_password_salt": None,
        "auto_enroll_local_worker": auto_enroll,
    }
    target = data_dir / helpers.PENDING_SETUP_FILENAME
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def _run_command(
    data_dir: Path,
    *,
    extra_args: list[str] | None = None,
):
    """Invoke ``Command.handle`` with ``--data-dir`` parsed via argparse."""
    from django.core.management import call_command
    args = ["apply_pending_setup", "--data-dir", str(data_dir)]
    if extra_args:
        args.extend(extra_args)
    call_command(*args)
