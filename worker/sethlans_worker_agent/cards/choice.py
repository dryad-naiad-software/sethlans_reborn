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

``apply_manager_dismissals`` applies the ``dismissed_card_alerts`` of each
heartbeat answer through ``dismiss_alerts`` (FR-CR-6).
"""
import logging
import re
import threading
from typing import Iterable, List, Optional

from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import store

logger = logging.getLogger(__name__)

MAX_DISMISSALS = 16
_ALERT_ID = re.compile(r'[0-9a-f]{32}')
_malformed_lock = threading.Lock()  # leaf
_malformed_warned = False           # a malformed dismissed_card_alerts was warned about; under _malformed_lock


def _request_rebuild() -> None:
    """Ask for slots built from the new choice (outside every store lock)."""
    rebuild.request_rebuild()


def save_choice(method: str, gpu_mode: str, ticked_keys, shown_keys, base_revision: int, inventory,
                last_request: Optional[dict] = None, manager_request: bool = False) -> int:
    """Save the choice (``store.save`` rules and errors) and return the new revision."""
    revision = store.save(method, gpu_mode, ticked_keys, shown_keys, inventory, base_revision,
                          last_request=last_request, manager_request=manager_request)
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


def _valid_dismissals(value) -> bool:
    return isinstance(value, list) and len(value) <= MAX_DISMISSALS and all(
        isinstance(item, str) and _ALERT_ID.fullmatch(item) is not None for item in value)


def _warn_malformed_once() -> None:
    global _malformed_warned
    with _malformed_lock:
        if _malformed_warned:
            return
        _malformed_warned = True
    logger.warning("The manager's answer has a dismissed_card_alerts field that is not a list of at most %d alert "
                   "ids; it is ignored.", MAX_DISMISSALS)


def apply_manager_dismissals(answer) -> List[dict]:
    """FR-CR-6: dismiss the open alerts that ``dismissed_card_alerts`` of a heartbeat answer names.

    ``answer`` is the manager's answer to the authenticated heartbeat or
    registration, never anything anonymous. Each named open alert goes
    through ``dismiss_alerts`` (one INFO line each, a rebuild); unknown ids
    are ignored, so a repeated delivery dismisses nothing twice. A malformed
    field (not a list of at most 16 ids of 32 lower-case hex) is ignored with
    one WARNING until a well-formed field arrives. A failed write is logged;
    the manager sends the ids again with the next answer. Never raises.
    """
    global _malformed_warned
    if not isinstance(answer, dict) or answer.get('dismissed_card_alerts') is None:
        return []
    ids = answer['dismissed_card_alerts']
    if not _valid_dismissals(ids):
        _warn_malformed_once()
        return []
    with _malformed_lock:
        _malformed_warned = False
    try:
        if not set(ids) & {alert.get('id') for alert in store.snapshot().alerts}:
            return []  # nothing open is named (the usual case): no lock, no strict read
        return dismiss_alerts(ids, 'manager')
    except Exception:  # noqa: BLE001 - ConfigLockTimeoutError, OSError; the heartbeat goes on
        logger.warning("The graphics card alerts dismissed from the manager could not be removed yet; trying again "
                       "with the next heartbeat.", exc_info=True)
        return []


def reset_for_tests() -> None:
    """Forget that a malformed dismissal field was warned about. Test-only hook."""
    global _malformed_warned
    with _malformed_lock:
        _malformed_warned = False
