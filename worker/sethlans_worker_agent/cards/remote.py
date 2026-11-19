# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A graphics card change sent by the manager in a heartbeat answer (FR-CM-3..8, D14-D17).

``handle_response(answer)`` runs on the agent-loop thread after each heartbeat answer (never a registration
answer) and never raises. The answer's ``card_choice_request`` is evaluated once per id (D16): an id among the
last 8 evaluated (``compute.recent_request_ids`` and the memory) is not evaluated again, also when it arrives late
or out of order; the last outcome is what every heartbeat reports (``cards.request_outcome``). A request whose id
is not 32 lower-case hex is ignored with one WARNING (until a request with a valid id arrives).

Otherwise the checks of FR-CM-5 run in order and the first failing one decides:

1. shape (FR-CM-4; the manager is not trusted for it) -> ``invalid``; the receipt is logged only after it passed;
2. ``env_override``; 3. the switch (``not_allowed``);
4. a fresh OS probe only (no screen read, no Blender run) -> ``probe_failed`` when it or the inventory failed;
5. every ticked card present with the same ``vendor_id`` and ``device_id`` -> ``identity_mismatch``;
6. ``choice.save_choice(..., manager_request=True)`` (the worker pages' own path, FR-CM-6), which re-checks
   env override, the switch and the revision under the section lock: ``env_override``, ``not_allowed``,
   ``stale_revision``, ``card_held``, ``invalid`` or ``save_failed`` (lock timeout, write error); ``applied`` when
   saved, the outcome in the same write (revision + 1).

A refusal writes only ``last_request`` and the recent ids (kept in memory when that write fails). Request values
reach no shell, script or path and are logged through ``ascii()`` only. While ``config.json`` cannot be read (at
the snapshot or inside the save) or an Unreal render holds the machine, the request waits, with no outcome.
"""
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Optional

from sethlans_worker_agent import config_compute, config_store, unreal_bridge
from sethlans_worker_agent.cards import choice, inventory, os_probe, store, store_requests
from sethlans_worker_agent.cards.identity import is_card_key
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError

logger = logging.getLogger(__name__)

_HEX32 = re.compile(r'[0-9a-f]{32}')
_REQUESTED_BY = re.compile(r'user id=[0-9]{1,12}')
MAX_TIME_TEXT = 40
RECEIPT = "Graphics card change %s from the manager, requested by %s at %s."
APPLIED = "Graphics card change %s applied: method %s, mode %s, cards %s; revision %d."
REFUSED = "Graphics card change %s refused (%s): %s."
NO_ID = "Ignored a graphics card change from the manager with no valid id."
READ_FAILED = ("The worker's settings file could not be read; the graphics card change from the manager is handled "
               "with a later heartbeat.")
ENV_TEXT = "the graphics card choice is set by environment variables on this worker"
NOT_ALLOWED_TEXT = "this worker does not allow the manager to change its graphics card choice"
PROBE_TEXT = "the worker could not list its graphics cards; nothing was changed"
STALE_TEXT = "the choice was changed on the worker (revision {current}) after the manager's view (revision {base})"
SAVE_FAILED_TEXT = "the worker could not save the choice"

_warn_lock = threading.Lock()  # leaf
_no_id_warned = False          # under _warn_lock


def _ids_ok(entry) -> bool:
    return all(name in entry and (entry[name] is None or (isinstance(entry[name], int)
               and not isinstance(entry[name], bool) and 0 <= entry[name] <= 0xFFFF))
               for name in ('vendor_id', 'device_id'))


def _cards_ok(value) -> bool:
    if not isinstance(value, list) or len(value) > config_compute.MAX_ENTRIES:
        return False
    if not all(isinstance(entry, dict) and is_card_key(entry.get('key')) and _ids_ok(entry) for entry in value):
        return False
    return len({entry['key'] for entry in value}) == len(value)


def _shown_ok(value) -> bool:
    return (isinstance(value, list) and len(value) <= config_compute.MAX_ENTRIES
            and all(is_card_key(key) for key in value) and len(set(value)) == len(value))


def _requested_by(value) -> Optional[str]:
    return value if isinstance(value, str) and _REQUESTED_BY.fullmatch(value) else None


def _requested_at(value) -> Optional[str]:
    if not isinstance(value, str) or len(value) > MAX_TIME_TEXT or not value.isascii():
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return value if moment.utcoffset() is not None else None


def bad_field(request: dict) -> Optional[str]:
    """The first field that breaks the FR-CM-4 shape, or ``None``; unknown fields are ignored."""
    revision = request.get('base_revision')
    checks = (
        ('method', lambda: isinstance(request.get('method'), str) and request['method'] in config_compute.METHODS),
        ('gpu_mode', lambda: isinstance(request.get('gpu_mode'), str)
         and request['gpu_mode'] in config_compute.GPU_MODES),
        ('cards', lambda: _cards_ok(request.get('cards'))),
        ('shown', lambda: _shown_ok(request.get('shown'))
         and {entry['key'] for entry in request['cards']} <= set(request['shown'])),
        ('base_revision', lambda: isinstance(revision, int) and not isinstance(revision, bool)
         and 0 <= revision <= config_compute.MAX_REVISION),
        ('requested_by', lambda: _requested_by(request.get('requested_by')) is not None),
        ('requested_at', lambda: _requested_at(request.get('requested_at')) is not None),
    )
    for name, passes in checks:  # in order: the shown check reads the cards only after they passed
        if not passes():
            return name
    return None


def _record(request_id: str, request: dict, code: str, reason: str, revision: int) -> dict:
    """The FR-CM-7 outcome; ``requested_by`` and ``requested_at`` only in their valid forms (S2)."""
    return {'id': request_id, 'result': 'applied' if code == 'applied' else 'refused', 'code': code,
            'reason': store_requests.printable(reason, store_requests.MAX_REASON), 'revision': revision,
            'requested_by': _requested_by(request.get('requested_by')),
            'requested_at': _requested_at(request.get('requested_at')),
            'finished_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def _refuse(request_id: str, request: dict, code: str, reason: str, revision: int) -> None:
    record = _record(request_id, request, code, reason, revision)
    logger.warning(REFUSED, ascii(request_id), ascii(code), ascii(record['reason']))
    try:
        store_requests.set_last_request(record)
    except (ConfigLockTimeoutError, OSError):
        logger.warning("The outcome of graphics card change %s could not be saved; it is kept until the worker "
                       "stops.", ascii(request_id), exc_info=True)


def _fresh_inventory():
    """A fresh OS probe joined with the Blender list the checks use; ``None`` when the probe failed (FR-CK-1)."""
    os_cards = os_probe.probe()
    if os_cards is None:
        return None
    from sethlans_worker_agent.capacity import card_blender  # capacity imports cards; keep the import lazy
    blender_cards, version = card_blender.known()
    return inventory.build(os_cards, blender_cards, version)


def identity_mismatch(cards, inv) -> Optional[str]:
    """Check 6: the reason for the first ticked card that is absent or another model, else ``None`` (D15)."""
    by_key = {card.key: card for card in inv.cards}
    for wanted in cards:
        card = by_key.get(wanted['key'])
        if card is None:
            return f"{wanted['key']} is not on this machine"
        if (card.vendor_id, card.device_id) != (wanted['vendor_id'], wanted['device_id']):
            pair = (card.vendor_id, card.device_id)
            ids = ':'.join(f'{value:04x}' if isinstance(value, int) else '?' for value in pair)
            return f"{wanted['key']} is now {card.name} ({ids})"
    return None


def _save(request_id: str, request: dict, inv, revision: int) -> None:
    """Checks 7-10 through ``choice.save_choice``; the applied outcome goes into the same write."""
    base = request['base_revision']
    ticked = [entry['key'] for entry in request['cards']]
    try:
        saved = choice.save_choice(request['method'], request['gpu_mode'], ticked, request['shown'], base, inv,
                                   last_request=_record(request_id, request, 'applied', '', base + 1),
                                   manager_request=True)
    except config_store.ConfigReadError:  # unreadable just now: no outcome, the next heartbeat retries
        os_probe.log_once(logger, logging.WARNING, READ_FAILED)
        return None
    except store.EnvOverride:
        return _refuse(request_id, request, 'env_override', ENV_TEXT, revision)
    except store.ManagerChangesOff:  # the switch was turned off during the probe (checked under the lock)
        return _refuse(request_id, request, 'not_allowed', NOT_ALLOWED_TEXT, revision)
    except store.StaleRevision as stale:
        return _refuse(request_id, request, 'stale_revision',
                       STALE_TEXT.format(current=stale.current, base=stale.base), stale.current)
    except store.CardHeld as held:
        return _refuse(request_id, request, 'card_held', str(held), revision)
    except ValueError as invalid:
        return _refuse(request_id, request, 'invalid', str(invalid), revision)
    except (ConfigLockTimeoutError, OSError):
        logger.warning("Graphics card change %s could not be saved.", ascii(request_id), exc_info=True)
        return _refuse(request_id, request, 'save_failed', SAVE_FAILED_TEXT, revision)
    mode = request['gpu_mode'] if len(ticked) >= 2 else 'split'  # as store.save keeps it
    logger.info(APPLIED, ascii(request_id), ascii(request['method']), ascii(mode), ascii(ticked), saved)


def _evaluate(request_id: str, request: dict, section) -> None:
    """Checks 3-10 of FR-CM-5, after the shape passed."""
    if section.env_override:
        return _refuse(request_id, request, 'env_override', ENV_TEXT, section.revision)
    if section.allow_manager_changes is not True:
        return _refuse(request_id, request, 'not_allowed', NOT_ALLOWED_TEXT, section.revision)
    inv = _fresh_inventory()
    if inv is None or inv.os_probe_failed:
        return _refuse(request_id, request, 'probe_failed', PROBE_TEXT, section.revision)
    mismatch = identity_mismatch(request['cards'], inv)
    if mismatch is not None:
        return _refuse(request_id, request, 'identity_mismatch', mismatch, section.revision)
    return _save(request_id, request, inv, section.revision)


def _valid_id(request) -> Optional[str]:
    """The request's id when it is 32 lower-case hex; otherwise one WARNING until a valid id arrives."""
    global _no_id_warned
    request_id = request.get('id') if isinstance(request, dict) else None
    valid = isinstance(request_id, str) and _HEX32.fullmatch(request_id) is not None
    with _warn_lock:
        warn = not valid and not _no_id_warned
        _no_id_warned = not valid
    if warn:
        logger.warning(NO_ID)
    return request_id if valid else None


def _handle(answer) -> None:
    request = answer.get('card_choice_request') if isinstance(answer, dict) else None
    if not request:  # absent, null or {}: nothing is waiting
        return
    request_id = _valid_id(request)
    if request_id is None:
        return
    section = store.snapshot()
    if section.read_failed:
        os_probe.log_once(logger, logging.WARNING, READ_FAILED)
        return
    if store_requests.already_evaluated(section, request_id):
        return  # evaluated once (D16), also a late older id; the last outcome is what the heartbeat reports
    if not unreal_bridge.drift_check_allowed():  # no card probe while an Unreal render holds the machine
        logger.debug("Graphics card change %s waits until the Unreal render ends.", ascii(request_id))
        return
    field = bad_field(request)
    if field is not None:
        return _refuse(request_id, request, 'invalid', f'the request is malformed ({field})', section.revision)
    logger.info(RECEIPT, ascii(request_id), ascii(request['requested_by']), ascii(request['requested_at']))
    _evaluate(request_id, request, section)


def handle_response(answer) -> None:
    """Apply or refuse the ``card_choice_request`` of a heartbeat answer (FR-CM-3); never raises."""
    try:
        _handle(answer)
    except Exception:  # noqa: BLE001 - the agent loop must go on
        logger.error("The graphics card change from the manager could not be handled.", exc_info=True)


def reset_for_tests() -> None:
    """Forget the no-id warning and the outcome kept in memory (``store_requests``). Test-only hook."""
    global _no_id_warned
    with _warn_lock:
        _no_id_warned = False
    store_requests.reset_memory_for_tests()
