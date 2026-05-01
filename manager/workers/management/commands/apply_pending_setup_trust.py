# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Filesystem-trust enrollment for ``apply_pending_setup`` (FR-APPLY2 step 4).

Split out so ``apply_pending_setup_helpers.py`` stays under the
300-line ceiling after the Spec 2 LOW follow-ups landed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from workers.management.commands.apply_pending_setup_helpers import (
    FilesystemTrustError,
)

logger = logging.getLogger(__name__)


def prime_runtime_state_for_auto_enroll() -> None:
    """Populate ``runtime_state`` so ``auto_enroll_local_worker`` works.

    Reads manager_id from the DB and the cert fingerprint from on-disk
    TLS material (generated on first call). Spec 2 security MED res. A:
    ``dev_mode=False`` is correct because the launcher has no ``--dev``
    flag and is the only caller of this subprocess.
    """
    from django.conf import settings as dj_settings
    from sethlans_manager import runtime_state
    from sethlans_manager.cert_utils import get_cert_fingerprint
    from sethlans_manager.tls_setup import setup_certificates
    from workers.models import ManagerSettings

    if runtime_state.manager_id is None:
        runtime_state.manager_id = (
            ManagerSettings.objects.get(pk=1).manager_id
        )
    if runtime_state.cert_fingerprint is None:
        manager_dir = Path(dj_settings.BASE_DIR)
        _, _, cert = setup_certificates(
            dev_mode=False,  # see invariant in docstring above
            manager_dir=manager_dir,
            project_root=manager_dir.parent,
        )
        runtime_state.cert_fingerprint = get_cert_fingerprint(cert)


def apply_filesystem_trust() -> None:
    """FR-APPLY2 step 4 — write the co-located worker's config.json."""
    from workers.services import auto_enroll, filesystem_trust
    try:
        prime_runtime_state_for_auto_enroll()
        envelope = auto_enroll.auto_enroll_local_worker()
        filesystem_trust.write_worker_config(
            config_path=filesystem_trust.get_worker_config_path(),
            api_token=envelope["api_token"],
            cert_fingerprint=envelope["cert_fingerprint"],
            manager_url=envelope["manager_url"],
            manager_id=envelope["manager_id"],
        )
    except Exception as exc:  # noqa: BLE001
        raise FilesystemTrustError(
            f"filesystem trust enrollment failed: "
            f"{exc.__class__.__name__}",
        ) from None


__all__ = [
    "apply_filesystem_trust",
    "prime_runtime_state_for_auto_enroll",
]
