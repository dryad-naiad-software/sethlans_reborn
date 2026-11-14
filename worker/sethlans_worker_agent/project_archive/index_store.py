# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Durable writes of ``index.json`` (Unreal foundation FR-PA-1, FR-PA-10).

The payload goes to an exclusively created temp file next to it, which is
fsync'ed, given the token user as owner (Windows; also when the worker
runs elevated) and then ``os.replace``'d over ``index.json``. Called with
the index lock held.
"""
import json
import logging
import os
import sys
import uuid

from shared import owner_only_acl

logger = logging.getLogger(__name__)

INDEX_SCHEMA = 1


def write_index(layout, entries) -> None:
    """Persist ``ready`` entries; failures are logged (the index is only a ledger)."""
    payload = {'schema': INDEX_SCHEMA, 'entries': {
        e.sha256: {'size_bytes': e.size_bytes, 'last_used': e.last_used, 'source_unc': e.source_unc}
        for e in sorted(entries, key=lambda item: item.sha256)}}
    temp = layout.root / f"{layout.index.name}.tmp-{uuid.uuid4().hex}"
    try:
        with open(temp, 'xb') as handle:
            handle.write(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
            handle.flush()
            os.fsync(handle.fileno())
        if sys.platform == 'win32':
            owner_only_acl.apply_owner_only(temp)
        os.replace(temp, layout.index)
    except OSError as exc:
        logger.error("Could not write the Unreal cache index: %s", exc)
        try:
            os.unlink(temp)
        except OSError:
            pass
