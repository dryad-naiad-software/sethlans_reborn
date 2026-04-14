# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-time helpers for the worker agent lifecycle.

Handles the transition between setup mode (browser wizard) and
normal operation mode. Extracted from ``agent.py`` to keep it
under the 300-line limit.
"""

import importlib
import logging

from sethlans_worker_agent import config_store
from sethlans_worker_agent.web_ui.setup.gate import init_gate, is_in_setup_mode
from sethlans_worker_agent.web_ui.setup.sentinel import is_setup_complete

logger = logging.getLogger(__name__)


def initialize_setup_gate() -> bool:
    """Initialize the setup gate. Returns True if setup is complete."""
    data_dir = config_store.get_data_dir()
    init_gate(data_dir)
    return not is_in_setup_mode()


def wait_for_browser_setup(shutdown_event, bind_address, port):
    """Block until the browser setup wizard completes or shutdown.

    Polls the sentinel file every 5 seconds. Logs the setup URL
    once so the user knows where to go.
    """
    data_dir = config_store.get_data_dir()
    logger.info(
        "Setup wizard available at https://%s:%d/setup",
        bind_address, port,
    )
    logger.info(
        "Complete setup via your browser, then the worker "
        "will start automatically."
    )
    while not shutdown_event.is_set():
        if is_setup_complete(data_dir):
            logger.info("Setup completed via browser wizard.")
            # Reload config so newly-persisted credentials
            # (api_token, cert_fingerprint, etc.) are live.
            _reload_config()
            return True
        shutdown_event.wait(5)
    return False


def _reload_config():
    """Reload the config module so post-enrollment values are live."""
    import importlib
    from sethlans_worker_agent import config as config_mod
    importlib.reload(config_mod)


def run_first_run_wizard_if_needed():
    """Run the first-run enrollment wizard on the main thread.

    Invariant: runs BEFORE any background threads are spawned so
    ``tls_adapter.reset_sessions()`` (per-thread) works correctly for
    pinning activation after enrollment (FR-23).

    On success, writes the setup sentinel and flips the setup gate
    so the worker's web UI is fully accessible after enrollment.

    Returns the wizard exit code (0 on success or "wizard not needed").
    """
    if config_store.get("enrollment.wizard_complete", False):
        return 0
    from sethlans_worker_agent import wizard
    logger.info("First-run enrollment wizard required.")
    code = wizard.run_wizard()
    if code != 0:
        logger.error("Enrollment wizard exited with code %d.", code)
        return code
    # Re-read config so the new token/fingerprint are live in this
    # process. Downstream modules that cached ``config`` references
    # still see a fresh module object after this reload.
    from sethlans_worker_agent import config as config_mod
    importlib.reload(config_mod)
    # Write sentinel and flip the setup gate so the worker's web UI
    # (dashboard, /api/status, control endpoints) is accessible.
    _finalize_setup_after_wizard()
    return 0


def _finalize_setup_after_wizard():
    """Write sentinel and flip the gate after CLI/unattended wizard."""
    from sethlans_worker_agent.web_ui.setup.gate import mark_setup_complete
    from sethlans_worker_agent.web_ui.setup.sentinel import (
        create_sentinel, is_setup_complete,
    )
    data_dir = config_store.get_data_dir()
    if not is_setup_complete(data_dir):
        create_sentinel(
            data_dir,
            topology="worker_only",
            checkpoints=["enrolled", "wizard_complete"],
        )
    mark_setup_complete()
