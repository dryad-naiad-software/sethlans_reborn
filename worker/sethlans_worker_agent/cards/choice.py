# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One code path for saving the card choice (FR-CM-6).

``save_choice`` saves through ``store.save`` and, once that returned and the
section lock is released, asks for the new slots. The worker's setup page, its
settings page and the manager's change request all save through here.

The live slot rebuild is not built yet: until it is, a saved choice takes
effect when the worker restarts, and ``_request_rebuild`` says so in the log.
"""
import logging
from typing import Optional

from sethlans_worker_agent.cards import store

logger = logging.getLogger(__name__)


def _request_rebuild() -> None:
    """Ask for slots built from the new choice (outside every store lock)."""
    logger.info("The new graphics card choice takes effect when the worker restarts.")


def save_choice(method: str, gpu_mode: str, ticked_keys, shown_keys, base_revision: int, inventory,
                last_request: Optional[dict] = None) -> int:
    """Save the choice (``store.save`` rules and errors) and return the new revision."""
    revision = store.save(method, gpu_mode, ticked_keys, shown_keys, inventory, base_revision,
                          last_request=last_request)
    _request_rebuild()
    return revision
