# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker's compute choice: the ``compute`` section of ``config.json`` (FR-CC-1, FR-CC-2).

``load()`` reads the section and applies the overrides ``SETHLANS_COMPUTE_METHOD``, ``_GPU_MODE``, ``_CARDS``
(comma-separated PCI addresses) and ``_ALLOW_MANAGER_CHANGES``, each replacing its own key; ``env_override`` is
true when any of the first three is set. The default method is ``cpu``, as in the original Sethlans. An invalid
value (file or environment) is logged at ERROR once, naming the key, and the worker runs with ``method = cpu``,
never a GPU guess; an invalid switch means ``false``. Only ``cards.store`` writes the section; reads take no lock.
An unreadable ``config.json`` (C-20) gives defaults marked ``read_failed`` (checks and GETs skip their pass);
``load(strict=True)`` raises ``config_store.ConfigReadError`` instead (the writers).
"""
import dataclasses
import logging
import os
from typing import Optional, Tuple

from sethlans_worker_agent import config_store
from sethlans_worker_agent.cards.alert_records import distinct_alerts, valid_alert
from sethlans_worker_agent.cards.identity import is_card_key, parse_key_list
from sethlans_worker_agent.cards.os_probe import log_once

logger = logging.getLogger(__name__)

METHODS, GPU_MODES = ('cpu', 'gpu', 'cpu_gpu'), ('split', 'combined')
MAX_ENTRIES, MAX_REVISION = 16, 2 ** 31 - 1
ENV_METHOD = 'SETHLANS_COMPUTE_METHOD'
ENV_GPU_MODE = 'SETHLANS_COMPUTE_GPU_MODE'
ENV_CARDS = 'SETHLANS_COMPUTE_CARDS'
ENV_ALLOW = 'SETHLANS_COMPUTE_ALLOW_MANAGER_CHANGES'
_ALLOW_KEY = 'compute.allow_manager_changes'


@dataclasses.dataclass(frozen=True)
class ComputeSection:
    """The effective choice; ``cards`` are saved records, from SETHLANS_COMPUTE_CARDS keys only."""
    method: str = 'cpu'
    gpu_mode: str = 'split'
    cards: Tuple[dict, ...] = ()
    known: Tuple[str, ...] = ()
    revision: int = 0
    allow_manager_changes: bool = True
    last_request: Optional[dict] = None
    recent_request_ids: Tuple[str, ...] = ()  # the last 8 manager request ids evaluated (D16); not validated beyond
    alerts: Tuple[dict, ...] = ()
    env_override: bool = False
    env_cards: bool = False
    allow_from_env: bool = False
    invalid: Tuple[str, ...] = ()
    read_failed: bool = False  # config.json could not be read: these are defaults (C-20)

    @property
    def ticked_keys(self) -> Tuple[str, ...]:
        return tuple(record['key'] for record in self.cards)


def _int_in(value, low, high) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _valid_record(record) -> bool:
    if not isinstance(record, dict) or not is_card_key(record.get('key')):
        return False
    ids_ok = all(record.get(f) is None or _int_in(record.get(f), 0, 0xFFFF) for f in ('vendor_id', 'device_id'))
    revision_ok = record.get('revision_id') is None or _int_in(record.get('revision_id'), 0, 0xFF)
    return ids_ok and revision_ok and isinstance(record.get('name', ''), str)


def _list_of(raw, name, check, invalid) -> tuple:
    value = raw.get(name, [])
    if not isinstance(value, list) or len(value) > MAX_ENTRIES or not all(check(item) for item in value):
        invalid.append(f'compute.{name}')
        return tuple(item for item in value if check(item))[:MAX_ENTRIES] if isinstance(value, list) else ()
    return tuple(value)


def _choice(value, allowed, name, invalid):  # value when allowed, else the first, with ``name`` noted invalid
    if value in allowed:
        return value
    invalid.append(name)
    return allowed[0]


def _from_file(raw: dict, invalid: list) -> dict:
    """Field values from the saved section; each invalid key is added to ``invalid``."""
    revision, last_request = raw.get('revision', 0), raw.get('last_request')
    if not _int_in(revision, 0, MAX_REVISION):
        invalid.append('compute.revision')
        revision = 0
    if last_request is not None and not isinstance(last_request, dict):
        invalid.append('compute.last_request')
        last_request = None
    cards = _list_of(raw, 'cards', _valid_record, invalid)
    if len({record['key'] for record in cards}) != len(cards):
        invalid.append('compute.cards')
        cards = tuple({record['key']: record for record in cards}.values())
    allow, recent = raw.get('allow_manager_changes', True), raw.get('recent_request_ids')
    if not isinstance(allow, bool):
        invalid.append(_ALLOW_KEY)
    recent = tuple(item for item in recent if isinstance(item, str))[-8:] if isinstance(recent, list) else ()
    return {'method': _choice(raw.get('method', 'cpu'), METHODS, 'compute.method', invalid),
            'gpu_mode': _choice(raw.get('gpu_mode', 'split'), GPU_MODES, 'compute.gpu_mode', invalid),
            'cards': cards, 'known': _list_of(raw, 'known', is_card_key, invalid),
            'revision': revision, 'allow_manager_changes': allow is True, 'last_request': last_request,
            'recent_request_ids': recent,
            'alerts': distinct_alerts(_list_of(raw, 'alerts', valid_alert, invalid), logger)}


def _apply_env(values: dict, environ, invalid: list) -> dict:
    for name, env_name, allowed in (('method', ENV_METHOD, METHODS), ('gpu_mode', ENV_GPU_MODE, GPU_MODES)):
        if env_name in environ:
            values[name] = _choice(environ[env_name].strip().lower(), allowed, env_name, invalid)
    if ENV_CARDS in environ:
        keys = parse_key_list(environ[ENV_CARDS], MAX_ENTRIES)
        if keys is None:
            invalid.append(ENV_CARDS)
        values['cards'] = tuple({'key': key} for key in keys or ())
    if ENV_ALLOW in environ:
        text = environ[ENV_ALLOW].strip().lower()
        values['allow_manager_changes'] = _choice(text, ('false', 'true'), ENV_ALLOW, invalid) == 'true'
    values.update(env_cards=ENV_CARDS in environ, allow_from_env=ENV_ALLOW in environ,
                  env_override=any(name in environ for name in (ENV_METHOD, ENV_GPU_MODE, ENV_CARDS)))
    return values


def parse(raw, environ) -> ComputeSection:
    """The effective section from the saved section ``raw`` and the environment."""
    invalid: list = []
    if raw is not None and not isinstance(raw, dict):
        invalid.append('compute')
    values = _apply_env(_from_file(raw if isinstance(raw, dict) else {}, invalid), environ, invalid)
    for name in invalid:
        effect = "the manager may not change the card choice" if name in (ENV_ALLOW, _ALLOW_KEY) \
            else "this worker renders on the CPU only"
        log_once(logger, logging.ERROR, "Invalid graphics card setting %s; %s until it is fixed.", name, effect)
    if any(name not in (ENV_ALLOW, _ALLOW_KEY) for name in invalid):
        values['method'] = 'cpu'
    return ComputeSection(invalid=tuple(invalid), **values)


def _read_raw():
    """The saved ``compute`` section as stored (any type), or ``None``; ``ConfigReadError`` when unreadable."""
    return config_store.load_strict().get('compute')


def load(strict: bool = False) -> ComputeSection:
    """The effective section, read afresh (no lock); unreadable: defaults with ``read_failed``, or raise if ``strict``."""
    try:
        return parse(_read_raw(), os.environ)
    except config_store.ConfigReadError as error:
        if strict:
            raise
        logger.warning("The graphics card settings could not be read (%s); this pass uses no saved choice.", error)
        return dataclasses.replace(parse(None, os.environ), read_failed=True)
