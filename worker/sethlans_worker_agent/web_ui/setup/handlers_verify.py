# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard verification handler.

POST /api/setup/verify/ -- run verification checks and finalize
                           setup if all required checks pass.

On success, writes the sentinel file, flips the setup gate open,
and marks ``enrollment.wizard_complete`` in config_store so the
TTY wizard is skipped on future starts.

Phase 4c of the Waitress migration: this handler is sync WSGI.
Verification is a mutation (it writes the sentinel and flips the
gate), so the full body runs inside :func:`setup_mutation_lock`;
concurrent POSTs return ``409 Conflict``.
"""

import logging
from typing import Callable, Iterable

from sethlans_worker_agent import config_store
from sethlans_worker_agent.web_ui.http_helpers_wsgi import send_json_wsgi
from sethlans_worker_agent.web_ui.setup.gate import mark_setup_complete
from sethlans_worker_agent.web_ui.setup.sentinel import create_sentinel
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    get_current_topology,
    get_current_checkpoints,
)
from sethlans_worker_agent.web_ui.setup.lock import (
    setup_mutation_lock,
)

logger = logging.getLogger(__name__)


def handle_verify(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """POST /api/setup/verify/ -- Run verification and finalize."""
    with setup_mutation_lock() as acquired:
        if not acquired:
            return send_json_wsgi(
                start_response,
                {"error": "Setup mutation in progress; retry after "
                          "current operation completes."},
                409,
            )

        checks = []

        # Check 1: enrollment -- config has api_token + cert_fingerprint
        token = config_store.get("manager.api_token")
        fingerprint = config_store.get("manager.cert_fingerprint")
        checks.append({
            "name": "enrollment",
            "label": "Manager enrollment",
            "passed": bool(token and fingerprint),
        })

        # Check 2: password configured
        from sethlans_worker_agent.web_ui.auth import is_password_configured
        checks.append({
            "name": "password",
            "label": "UI password",
            "passed": is_password_configured(),
        })

        # Check 3: blender available (optional, not required)
        from sethlans_worker_agent.tool_manager import tool_manager_instance
        blenders = tool_manager_instance.scan_for_local_blenders()
        checks.append({
            "name": "blender",
            "label": "Blender installed",
            "passed": len(blenders) > 0,
            "optional": True,
        })

        all_passed = all(
            c["passed"] for c in checks if not c.get("optional")
        )

        if all_passed:
            data_dir = config_store.get_data_dir()
            topology = get_current_topology() or "worker_only"
            checkpoints = get_current_checkpoints() + ["verified"]

            create_sentinel(data_dir, topology, checkpoints)
            mark_setup_complete()

            # Mark enrollment wizard complete so TTY wizard is skipped
            config_store.set("enrollment.wizard_complete", True)
            logger.info(
                "Setup wizard: verification passed, setup complete.",
            )

        return send_json_wsgi(start_response, {
            "checks": checks,
            "all_passed": all_passed,
        })
