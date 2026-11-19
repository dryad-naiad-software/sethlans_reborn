# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One code path for saving the card choice and for dismissing alerts (FR-CM-6, FR-CK-7).

``save_choice`` saves through ``store.save`` and, once that returned and the
section lock is released, requests a live slot rebuild
(``capacity.rebuild.request_rebuild``, FR-CL-4). The worker's setup page,
its settings page and the manager's change request all save through here.

``dismiss_alerts`` removes open alerts through ``store.remove_alerts``, logs
one INFO line per removed alert and requests a rebuild, so a held card
returns to use if its state is now ``ok`` (O8). A card that is still missing
or changed is alerted again at the next check (FR-CK-3).
"""
import logging
from typing import Iterable, List, Optional

from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import store

logger = logging.getLogger(__name__)


def _request_rebuild() -> None:
    """Ask for slots built from the new choice (outside every store lock)."""
    rebuild.request_rebuild()


def save_choice(method: str, gpu_mode: str, ticked_keys, shown_keys, base_revision: int, inventory,
                last_request: Optional[dict] = None) -> int:
    """Save the choice (``store.save`` rules and errors) and return the new revision."""
    revision = store.save(method, gpu_mode, ticked_keys, shown_keys, inventory, base_revision,
                          last_request=last_request)
    _request_rebuild()
    return revision


def dismiss_alerts(alert_ids: Iterable[str], source: str) -> List[dict]:
    """Remove these open alerts (``store.remove_alerts`` errors); the removed records.

    Unknown ids are ignored. A rebuild is requested only when an alert was
    removed, after the section lock is released.
    """
    removed = store.remove_alerts(alert_ids)
    for alert in removed:
        logger.info("Graphics card alert %s dismissed from the %s: %s for %s (%s).", alert['id'], source,
                    alert['kind'], alert['key'], ascii(alert.get('name') or ''))
    if removed:
        _request_rebuild()
    return removed
