# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``cards`` key of the worker heartbeat (worker card selection FR-CR-1, FR-CR-3).

Self-reported and untrusted: reduced to a fixed shape and stored in
``Worker.card_status`` for display only, on the caller's own row, with the
handler's own save.  Never writes the manager-owned fields (D24, see
``heartbeat_card_sync``).  A report that is not an object with ``schema ==
1`` leaves the stored value unchanged; unknown keys are dropped at every
level; an invalid list entry is dropped and the rest kept; an invalid
top-level value is left out; strings are capped and sanitised.
``request_outcome`` is not stored here (FR-CM-13).  States are FR-CR-1's
plus ``unknown`` (a ticked card missing after a failed OS probe, C-24a); a
``lost`` card is a card entry carrying its saved record.
"""
import logging
import re
import threading
from datetime import datetime

from ..card_keys import is_card_key
from ..serializers.worker_card_schemas import (
    CARD_ALERT_KINDS, CARD_BACKENDS, CARD_GPU_MODES, CARD_METHODS, CARD_NOTICE_KINDS,
    CARD_STATES as _STATES,
)
from ..strict_json import _int_in, _is_int
from .heartbeat_unreal import MAX_REASON_LENGTH, _sanitize_reason

logger = logging.getLogger(__name__)

SCHEMA = 1
MAX_ENTRIES = 16
MAX_NAME_LENGTH = 128
MAX_PCI_ID = 0xFFFF
MAX_REVISION = 2 ** 31 - 1
MAX_RAISED_AT_LENGTH = 40
METHODS, GPU_MODES, CARD_STATES = frozenset(CARD_METHODS), frozenset(CARD_GPU_MODES), frozenset(_STATES)
BACKENDS, ALERT_KINDS, NOTICE_KINDS = frozenset(CARD_BACKENDS), frozenset(CARD_ALERT_KINDS), frozenset(CARD_NOTICE_KINDS)
_ALERT_ID = re.compile(r'[0-9a-f]{32}', re.ASCII)
_WARNING_CODE = re.compile(r'[a-z_]{1,32}', re.ASCII)

# ``_INVALID``: the value makes its entry invalid.  ``_REQUIRED``: the
# entry is invalid without the field (other absent fields get a default).
_INVALID = object()
_REQUIRED = object()
# Workers whose unusable report was logged at WARNING; later ones are DEBUG until a usable one.
_warned_workers = set()
_warned_lock = threading.Lock()


def is_alert_id(value) -> bool:
    """True for an alert id: 32 lower-case hex characters."""
    return isinstance(value, str) and _ALERT_ID.fullmatch(value) is not None


def _text(limit):
    def check(raw):
        return _sanitize_reason(raw)[:limit] if isinstance(raw, str) else _INVALID
    return check


def _when(predicate, nullable=False):
    """A check keeping a value that satisfies ``predicate`` (and ``None`` if ``nullable``)."""
    def check(raw):
        return raw if (nullable and raw is None) or predicate(raw) else _INVALID
    return check


def _choice(allowed, nullable=False):
    return _when(lambda raw: isinstance(raw, str) and raw in allowed, nullable)


_bool = _when(lambda raw: isinstance(raw, bool))
_bool_or_null = _when(lambda raw: isinstance(raw, bool), nullable=True)
_pci_id = _when(lambda raw: _int_in(raw, 0, MAX_PCI_ID), nullable=True)
_revision = _when(lambda raw: _int_in(raw, 0, MAX_REVISION))
_key = _when(is_card_key)
_key_or_null = _when(is_card_key, nullable=True)
_alert_id = _when(is_alert_id)
_warning_code = _when(lambda raw: isinstance(raw, str) and _WARNING_CODE.fullmatch(raw) is not None)


def _raised_at(raw):
    """An aware ISO 8601 datetime of at most 40 characters in ``isoformat()`` form, else ``None``.

    Never drops the alert: one from a damaged or hand-edited worker store
    still holds its card there, so it must stay visible and dismissable here.
    """
    if not isinstance(raw, str) or len(raw) > MAX_RAISED_AT_LENGTH:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.utcoffset() is not None else None


_NAME = _text(MAX_NAME_LENGTH)
_LONG_TEXT = _text(MAX_REASON_LENGTH)

# (field, check, default or _REQUIRED), in the stored order.
_CARD_FIELDS = (
    ('key', _key, _REQUIRED), ('name', _NAME, ''), ('vendor_id', _pci_id, None), ('device_id', _pci_id, None),
    ('ticked', _bool, False), ('state', _choice(CARD_STATES), _REQUIRED), ('blender', _bool, False),
    ('backend', _choice(BACKENDS, nullable=True), None), ('note', _LONG_TEXT, ''),
    ('screen', _bool_or_null, None), ('primary_screen', _bool_or_null, None),
)
_ALERT_FIELDS = (
    ('id', _alert_id, _REQUIRED), ('kind', _choice(ALERT_KINDS), _REQUIRED), ('key', _key, _REQUIRED),
    ('name', _NAME, ''), ('detail', _LONG_TEXT, ''), ('raised_at', _raised_at, None),
)
_NOTICE_FIELDS = (('kind', _choice(NOTICE_KINDS), _REQUIRED), ('key', _key, _REQUIRED), ('name', _NAME, ''))
_WARNING_FIELDS = (('code', _warning_code, _REQUIRED), ('key', _key_or_null, None), ('text', _LONG_TEXT, _REQUIRED))
# (list name, entry fields, fields that must be unique across the list).
_LISTS = (
    ('cards', _CARD_FIELDS, ('key',)),
    ('alerts', _ALERT_FIELDS, ('id',)),
    ('notices', _NOTICE_FIELDS, ('kind', 'key')),
    ('warnings', _WARNING_FIELDS, ()),
)
# Top-level values; an absent or invalid one is left out.
_TOP_FIELDS = (
    ('method', _choice(METHODS)), ('gpu_mode', _choice(GPU_MODES)), ('env_override', _bool),
    ('revision', _revision), ('manager_changes_allowed', _bool), ('rebuild_waiting', _bool),
    ('problem', _LONG_TEXT),
)


def _entry(raw, fields):
    """Return the cleaned entry, or ``None`` when it is invalid."""
    if not isinstance(raw, dict):
        return None
    entry = {}
    for name, check, default in fields:
        if name not in raw:
            if default is _REQUIRED:
                return None
            entry[name] = default
            continue
        value = check(raw[name])
        if value is _INVALID:
            return None
        entry[name] = value
    return entry


def _entries(raw, fields, unique, label):
    """Clean one list: invalid and repeated entries dropped, at most 16 kept."""
    if not isinstance(raw, list):
        return []
    kept, seen = [], set()
    for item in raw:
        entry = _entry(item, fields)
        identity = tuple(entry[name] for name in unique) if entry else None
        if entry is None or (unique and identity in seen):
            logger.debug("Dropping invalid card report %s entry: %.200r", label, item)
            continue
        if len(kept) == MAX_ENTRIES:
            logger.debug("Dropping card report %s entries beyond %d", label, MAX_ENTRIES)
            break
        seen.add(identity)
        kept.append(entry)
    return kept


def validate_card_report(raw):
    """Reduce a reported ``cards`` object to its stored shape, else ``None``."""
    if not isinstance(raw, dict) or not _is_int(raw.get('schema')) or raw['schema'] != SCHEMA:
        return None
    report = {'schema': SCHEMA}
    for name, check in _TOP_FIELDS:
        if name in raw:
            value = check(raw[name])
            if value is not _INVALID:
                report[name] = value
    for name, fields, unique in _LISTS:
        report[name] = _entries(raw.get(name), fields, unique, name)
    return report


def apply_card_report(worker, data):
    """Set ``worker.card_status`` from the request's ``cards``; return the field names.

    Called from both heartbeat handlers.  A request without ``cards``, or
    with a report that is not usable, leaves the stored value unchanged.
    """
    if 'cards' not in data:
        return []
    report = validate_card_report(data['cards'])
    with _warned_lock:
        first = worker.pk not in _warned_workers
        if report is None:
            _warned_workers.add(worker.pk)
        else:
            _warned_workers.discard(worker.pk)
    if report is None:
        (logger.warning if first else logger.debug)("Ignoring invalid cards report from worker %s", worker.hostname)
        return []
    worker.card_status = report
    return ['card_status']
