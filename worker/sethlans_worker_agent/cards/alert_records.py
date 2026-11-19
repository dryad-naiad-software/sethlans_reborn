# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Saved graphics card alert records (FR-CK-5): the kinds, the shape check and de-duplication.

- ``ALERT_KINDS``: the alert kinds a record may have.
- ``valid_alert(alert)``: a dict with a known ``kind``, a card ``key`` and a
  32 lower-case hex ``id``.
- ``distinct_alerts(alerts, log)``: the first alert per ``id`` and per
  ``(kind, key)`` (a hand-edited ``config.json``), with one WARNING per
  process on ``log``.

Pure apart from that once-only log line; holds no lock.
"""
import logging
import re

from sethlans_worker_agent.cards.identity import is_card_key
from sethlans_worker_agent.cards.os_probe import log_once

ALERT_KINDS = ('card_lost', 'card_changed', 'card_lost_blender', 'adapter_mismatch')
_ALERT_ID = re.compile(r'[0-9a-f]{32}')


def valid_alert(alert) -> bool:
    return (isinstance(alert, dict) and alert.get('kind') in ALERT_KINDS and is_card_key(alert.get('key'))
            and isinstance(alert.get('id'), str) and _ALERT_ID.fullmatch(alert['id']) is not None)


def distinct_alerts(alerts: tuple, log: logging.Logger) -> tuple:
    """The first alert per id and per ``(kind, key)``; one WARNING per process on ``log`` when any was dropped."""
    seen: set = set()
    kept = []
    for alert in alerts:
        marks = {alert['id'], (alert['kind'], alert['key'])}
        if not marks & seen:
            seen |= marks
            kept.append(alert)
    if len(kept) != len(alerts):
        log_once(log, logging.WARNING, "config.json lists a graphics card alert more than once (the same id, or "
                 "the same kind for the same card); only the first is used.")
    return tuple(kept)
