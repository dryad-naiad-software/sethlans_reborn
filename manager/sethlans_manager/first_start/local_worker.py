# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-FS-3: trust for the local worker of a ``manager_worker`` machine.

Only when ``[setup] mode`` is ``manager_worker``. The local worker's
``config.json`` (its identity folder, D7) is checked, and filesystem
trust runs again when:

* it has no ``manager.api_token``;
* its ``manager.manager_id`` is not this manager's (a new database);
* its token is no longer in the ``Token`` table.

Trust is today's pair: ``auto_enroll.auto_enroll_local_worker`` (a fresh
token for the local worker's account) and
``filesystem_trust.write_worker_config`` (merged into ``config.json``,
other keys kept). It needs ``runtime_state.manager_id`` and the
certificate fingerprint, which ``run_manager._prepare_runtime`` has set.
A failure is exit 11. The token is never logged.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from sethlans_manager.error_text import short_error
from sethlans_manager.first_start.common import FirstStartError
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED

logger = logging.getLogger(__name__)

TRUSTED_MODE = "manager_worker"


def _not_trusted(exc: Exception) -> FirstStartError:
    return FirstStartError(EXIT_FIRST_START_FAILED, f"the local worker could not be trusted: {short_error(exc)}")


def _manager_section(config_path: Path) -> dict:
    """The ``manager`` object of ``config.json``; empty when missing or unreadable."""
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("The local worker's config %s cannot be read (%s); trusting it again",
                       config_path, type(exc).__name__)
        return {}
    manager = loaded.get("manager") if isinstance(loaded, dict) else None
    return manager if isinstance(manager, dict) else {}


def trust_reason(config_path: Path, manager_id: str) -> Optional[str]:
    """Why trust must run again, or ``None`` when the local worker is trusted."""
    from rest_framework.authtoken.models import Token

    manager = _manager_section(config_path)
    token = manager.get("api_token")
    if not isinstance(token, str) or not token:
        return "it has no token"
    if str(manager.get("manager_id") or "") != manager_id:
        return "it is enrolled with another manager"
    if not Token.objects.filter(key=token).exists():
        return "its token no longer exists"
    return None


def ensure_local_worker_trust(mode: Optional[str]) -> bool:
    """FR-FS-3. True when trust ran (the worker's ``config.json`` was written)."""
    if mode != TRUSTED_MODE:
        return False
    from sethlans_manager import runtime_state
    from workers.services import auto_enroll, filesystem_trust

    try:
        config_path = filesystem_trust.get_worker_config_path()  # creates the folder; may fail
    except Exception as exc:  # noqa: BLE001 - any failure stops the start with exit 11
        raise _not_trusted(exc) from None
    reason = trust_reason(config_path, str(runtime_state.manager_id))
    if reason is None:
        logger.info("The local worker is trusted already (%s)", config_path)
        return False
    logger.info("Trusting the local worker again: %s", reason)
    try:
        envelope = auto_enroll.auto_enroll_local_worker()
        filesystem_trust.write_worker_config(
            config_path=config_path,
            api_token=envelope["api_token"],
            cert_fingerprint=envelope["cert_fingerprint"],
            manager_url=envelope["manager_url"],
            manager_id=envelope["manager_id"],
        )
    except Exception as exc:  # noqa: BLE001 - any failure stops the start with exit 11
        raise _not_trusted(exc) from None
    logger.info("Wrote the local worker's enrollment to %s", config_path)
    return True
