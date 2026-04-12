# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
First-run enrollment wizard (FR-30, FR-31, FR-32).

Runs on the main thread before any background threads are spawned.
Dispatches between an interactive TTY path (prompts for key via
``getpass``) and an unattended env-var path (Docker / systemd /
SCCM). Both paths share the same enrollment call into
:func:`enrollment_client.enroll`.

Exit codes (returned to caller, not ``sys.exit``ed directly):
  0 — success
  2 — no managers discovered on the network (interactive only)
  3 — user declined to retry after a failed attempt
  4 — unattended mode failed (missing env vars, all backoff
      attempts exhausted, or bad discovery result)
  5 — ``KeyboardInterrupt`` (Ctrl+C) during any prompt
"""

import getpass
import logging
import os
import sys
import time
from typing import Dict, Optional, Tuple

from sethlans_worker_agent import config_store, enrollment_client, hardware_detection
from sethlans_worker_agent.multicast_listener import MulticastListener

logger = logging.getLogger(__name__)

WIZARD_OK = 0
WIZARD_NO_MANAGERS = 2
WIZARD_USER_DECLINED = 3
WIZARD_UNATTENDED_FAILED = 4
WIZARD_CANCELLED = 5

# Exponential backoff for unattended retry per FR-32. Attempt 1 is
# immediate; subsequent attempts sleep for the corresponding value
# before firing.
_BACKOFF_SCHEDULE = (0, 1, 3, 9, 27)

WORKER_VERSION = "alpha"


def run_wizard() -> int:
    """Run the first-run wizard. Returns the exit code (see module docstring)."""
    try:
        if sys.stdin.isatty():
            return _run_interactive()
        return _run_unattended()
    except KeyboardInterrupt:
        print("Enrollment cancelled.")
        return WIZARD_CANCELLED


# ---------------------------------------------------------------------------
# Interactive path
# ---------------------------------------------------------------------------


def _run_interactive() -> int:
    _print_header()
    print("Listening for manager announcements...")
    announcements = MulticastListener().discover()
    if not announcements:
        print(
            "No managers found. Check that a manager is running on "
            "your network and that UDP multicast 239.150.74.50:8082 "
            "is allowed."
        )
        return WIZARD_NO_MANAGERS

    managers = _format_manager_list(announcements)
    selected = _prompt_manager_selection(managers)
    if selected is None:
        return WIZARD_USER_DECLINED
    manager_url = _build_manager_url(selected)
    manager_id = selected.get("manager_id")

    while True:
        key = getpass.getpass("Enter enrollment key: ")
        try:
            result = enrollment_client.enroll(
                manager_url, key, hardware_detection.HOSTNAME,
            )
        except enrollment_client.EnrollmentError as e:
            print(f"Enrollment failed: {e}")
            if not _prompt_retry():
                return WIZARD_USER_DECLINED
            continue
        _persist_config(result, selected, manager_id)
        print("Enrollment successful. Worker will now start.")
        return WIZARD_OK


def _print_header() -> None:
    print("=" * 60)
    print(f"Sethlans Reborn Worker — enrollment wizard ({WORKER_VERSION})")
    print("=" * 60)


def _format_manager_list(announcements: Dict[str, dict]) -> list:
    managers = list(announcements.values())
    print()
    print(f"Discovered {len(managers)} manager(s):")
    for i, m in enumerate(managers, start=1):
        mid = m.get("manager_id", "?")
        mid_short = mid[:8] + "..." if len(str(mid)) > 8 else mid
        name = m.get("name", "Sethlans Manager")
        host = m.get("host", "?")
        ip = m.get("ip", "?")
        port = m.get("port", "?")
        print(
            f"  [{i}] {name}  {host}  {ip}:{port}  "
            f"(manager_id={mid_short})"
        )
    print()
    return managers


def _prompt_manager_selection(managers: list) -> Optional[dict]:
    if len(managers) == 1:
        answer = input("Use this manager? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return managers[0]
        return None
    while True:
        raw = input(f"Select manager [1-{len(managers)}]: ").strip()
        try:
            idx = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= idx <= len(managers):
            return managers[idx - 1]
        print("Out of range.")


def _prompt_retry() -> bool:
    answer = input("Retry? [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


# ---------------------------------------------------------------------------
# Unattended path
# ---------------------------------------------------------------------------


def _run_unattended() -> int:
    key = os.environ.get("SETHLANS_WORKER_ENROLLMENT_KEY")
    if not key:
        logger.error(
            "Unattended enrollment requires SETHLANS_WORKER_ENROLLMENT_KEY"
        )
        return WIZARD_UNATTENDED_FAILED

    manager = _resolve_unattended_manager()
    if manager is None:
        return WIZARD_UNATTENDED_FAILED
    manager_url, manager_meta, manager_id = manager

    last_error: Optional[Exception] = None
    for attempt, delay in enumerate(_BACKOFF_SCHEDULE, start=1):
        if delay:
            logger.info(
                "Retrying unattended enrollment in %ds "
                "(attempt %d/%d)",
                delay, attempt, len(_BACKOFF_SCHEDULE),
            )
            time.sleep(delay)
        try:
            result = enrollment_client.enroll(
                manager_url, key, hardware_detection.HOSTNAME,
            )
        except enrollment_client.EnrollmentError as e:
            last_error = e
            logger.warning(
                "Unattended enrollment attempt %d failed: %s",
                attempt, e,
            )
            continue
        _persist_config(result, manager_meta, manager_id)
        logger.info("Unattended enrollment successful.")
        return WIZARD_OK

    logger.error(
        "Unattended enrollment failed after %d attempts: %s",
        len(_BACKOFF_SCHEDULE), last_error,
    )
    return WIZARD_UNATTENDED_FAILED


def _resolve_unattended_manager() -> Optional[Tuple[str, dict, Optional[str]]]:
    """Return ``(manager_url, manager_meta, manager_id)`` or ``None``.

    ``manager_meta`` holds the host/port fields we want persisted to
    the config store; ``manager_id`` is optional (only known via
    multicast or via an explicit env var override).
    """
    env_host = os.environ.get("SETHLANS_MANAGER_HOST")
    env_port = os.environ.get("SETHLANS_MANAGER_PORT")
    env_manager_id = os.environ.get("SETHLANS_MANAGER_ID")

    if env_host and env_port:
        meta = {"host": env_host, "port": _coerce_port(env_port)}
        url = f"https://{env_host}:{meta['port']}/api/"
        return url, meta, env_manager_id

    announcements = MulticastListener().discover()
    if not announcements:
        logger.error(
            "Unattended enrollment: no manager host/port env vars and "
            "no multicast announcements received."
        )
        return None

    if env_manager_id and env_manager_id in announcements:
        chosen = announcements[env_manager_id]
    elif len(announcements) == 1:
        chosen = next(iter(announcements.values()))
    else:
        logger.error(
            "Unattended enrollment: multiple managers discovered and "
            "no SETHLANS_MANAGER_ID set for disambiguation."
        )
        return None
    return _build_manager_url(chosen), chosen, chosen.get("manager_id")


def _coerce_port(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 8080


# ---------------------------------------------------------------------------
# Shared config persistence
# ---------------------------------------------------------------------------


def _build_manager_url(manager: dict) -> str:
    host = manager.get("ip") or manager.get("host") or "127.0.0.1"
    port = manager.get("port") or 8080
    return f"https://{host}:{port}/api/"


def _persist_config(
    result: Dict[str, str],
    manager_meta: dict,
    manager_id: Optional[str],
) -> None:
    # Use set_many for a single atomic write — prevents half-configured
    # state if the process crashes mid-sequence.
    pairs = [
        ("manager.api_token", result["api_token"]),
        ("manager.cert_fingerprint", result["cert_fingerprint"]),
        (
            "manager.manager_id",
            result.get("manager_id") or manager_id or "",
        ),
    ]
    host = manager_meta.get("ip") or manager_meta.get("host")
    if host:
        pairs.append(("manager.host", host))
    port = manager_meta.get("port")
    if port:
        pairs.append(("manager.port", int(port)))
    pairs.append(("enrollment.wizard_complete", True))
    config_store.set_many(pairs)
