# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager's Sethlans share setting (Unreal foundation FR-MS-1, D3b).

``ManagerSettings.sethlans_share`` is the farm's single share root in
canonical UNC form, or ``''`` when not configured. It is the manager's
own value: where projects live and what routing compares each worker's
reported share against. It is never sent to workers.

**Readers re-validate.** :func:`configured_share` treats a stored value
that is not ``''`` and not a canonical UNC root as *not configured* and
logs it at ERROR once per process, so a value written by a data migration
or a shell session fails safe (the claim check, the routing note, asset
validation and the fingerprint command all read through here).
"""

import logging
import threading

from shared.unreal.unc import UncPath, validate_unc_dir

from ..models import ManagerSettings

logger = logging.getLogger(__name__)

_logged_invalid = set()
_logged_invalid_lock = threading.Lock()


def canonical_root(value) -> UncPath | None:
    """Return the ``UncPath`` of a canonical share root, else ``None``.

    ``''`` and any value that fails ``validate_unc_dir`` or differs from
    its canonical form give ``None``.
    """
    if not isinstance(value, str) or value == '':
        return None
    try:
        root = validate_unc_dir(value)
    except ValueError:
        return None
    return root if root.canonical == value else None


def _log_invalid_once(value) -> None:
    key = repr(value)[:200]
    with _logged_invalid_lock:
        if key in _logged_invalid:
            return
        _logged_invalid.add(key)
    logger.error(
        "Ignoring the stored Sethlans share %s: it is not a canonical UNC "
        "root. Treating the share as not configured; set it again with "
        "PUT /api/sethlans-share/.", key,
    )


def configured_share_root() -> UncPath | None:
    """Return the manager's share root, or ``None`` when not configured.

    One ``values_list`` read of the singleton row.
    """
    try:
        value = ManagerSettings.objects.values_list('sethlans_share', flat=True).get(pk=1)
    except ManagerSettings.DoesNotExist:
        return None
    if value == '':
        return None
    root = canonical_root(value)
    if root is None:
        _log_invalid_once(value)
    return root


def configured_share() -> str:
    """Return the canonical share root string, or ``''`` when not configured."""
    root = configured_share_root()
    return root.canonical if root is not None else ''
