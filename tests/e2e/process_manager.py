# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Subprocess lifecycle management for E2E tests.

Database-seed helpers live here. Process spawn/teardown was split into
sibling modules to keep every file under the project's 300-line cap:

* :mod:`tests.e2e.manager_process` — Waitress + Caddy spawn and the
  manager readiness probe.
* :mod:`tests.e2e.worker_process` — worker subprocess spawn and
  heartbeat-based readiness probe.
* :mod:`tests.e2e.process_lifecycle` — SIGTERM/SIGKILL teardown of
  process trees plus Caddy stop sequencing.

This module re-exports the public callables from those siblings so
existing imports like ``from tests.e2e.process_manager import
start_manager`` keep working without any test edits.
"""

import logging
import subprocess
import sys

import urllib3

# Re-export env helpers so existing imports keep working.
from tests.e2e.env_config import (  # noqa: F401
    REPO_ROOT,
    MANAGE_PY,
    RUN_MANAGER,
    WORKER_ENTRY,
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    ADMIN_EMAIL,
    find_free_port,
    build_manager_env,
    build_worker_env,
    generate_secrets,
)

# Suppress InsecureRequestWarning globally for E2E tests since the
# manager serves a self-signed TLS certificate.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


def run_management_command(env, *args):
    """Run a Django management command and return the result."""
    cmd = [sys.executable, MANAGE_PY] + list(args)
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        logger.error(
            "Management command %s failed:\nSTDOUT: %s\nSTDERR: %s",
            args, result.stdout, result.stderr,
        )
    return result


def setup_database(env):
    """Run migrate and createsuperuser for a fresh test database."""
    result = run_management_command(env, "migrate", "--run-syncdb")
    if result.returncode != 0:
        raise RuntimeError(
            f"migrate failed: {result.stderr}"
        )
    result = run_management_command(
        env, "createsuperuser", "--noinput",
    )
    if result.returncode != 0:
        # May fail if user already exists — that's OK.
        if "already taken" not in result.stderr.lower():
            logger.warning(
                "createsuperuser returned non-zero: %s", result.stderr
            )


# --- Backward-compat re-exports ---
# Tests and external callers may still import these from this module.
from tests.e2e.manager_process import (  # noqa: F401, E402
    _resolve_manager_data_dir,
    start_manager,
    wait_for_manager,
)
from tests.e2e.worker_process import (  # noqa: F401, E402
    start_worker,
    wait_for_worker,
)
from tests.e2e.process_lifecycle import (  # noqa: F401, E402
    _terminate_process_list,
    _kill_survivors,
    kill_process_tree,
)
