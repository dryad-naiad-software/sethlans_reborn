# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Agent-side helpers for wiring the Caddy supervisor.

Extracted from ``agent.py`` to keep that module under the 300-line
ceiling (it was at 297 before this phase). The two public helpers —
:func:`build_caddy_supervisor` and :func:`check_caddy_error` — are
thin adapters: the actual supervision behaviour lives in
``sethlans_worker_agent.caddy_supervisor``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sethlans_worker_agent import config
from sethlans_worker_agent.caddy_supervisor import CaddySupervisor
from sethlans_worker_agent.config_store import get_data_dir

try:  # Source mode: shared/ on sys.path.
    from shared.frozen_paths import get_caddy_path
except ImportError:  # pragma: no cover - frozen mode fallback
    from sethlans_worker_agent.frozen_paths import get_caddy_path

logger = logging.getLogger(__name__)


def build_caddy_supervisor(
    cert_path: Path, key_path: Path,
) -> CaddySupervisor:
    """Construct a :class:`CaddySupervisor` from worker config.

    The caller owns the supervisor's lifecycle (``start`` / ``stop``
    / ``error_event`` polling). This helper only assembles the
    constructor arguments from module-level config and the cert/key
    paths resolved by ``tls_setup``.
    """
    data_dir = Path(get_data_dir())
    caddyfile_path = data_dir / "caddy" / "Caddyfile"
    binary_path = Path(get_caddy_path())
    return CaddySupervisor(
        binary_path=binary_path,
        caddyfile_path=caddyfile_path,
        public_tls_port=config.CADDY_PUBLIC_TLS_PORT,
        loopback_plaintext_port=config.CADDY_LOOPBACK_PORT,
        waitress_upstream_port=config.WAITRESS_UPSTREAM_PORT,
        cert_path=cert_path,
        key_path=key_path,
        worker_data_dir=data_dir,
    )


def check_caddy_error(supervisor: Optional[CaddySupervisor]) -> bool:
    """Return True if the supervisor's error flag has been raised.

    Used by the agent main loop to detect retry-budget exhaustion.
    When True, the caller is expected to log and exit 1 per spec.
    """
    if supervisor is None:
        return False
    return supervisor.error_event.is_set()
