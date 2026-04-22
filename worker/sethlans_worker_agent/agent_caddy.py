# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Agent-side helpers for wiring the Caddy supervisor.

Extracted from ``agent.py`` to keep that module under the 300-line
ceiling. The two public helpers — :func:`build_caddy_supervisor` and
:func:`check_caddy_error` — are thin adapters: the actual supervision
behaviour lives in :mod:`shared.caddy_supervisor` (moved there in
manager spec Phase 3 so the Django launcher can reuse it).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sethlans_worker_agent import config
from sethlans_worker_agent.caddy_template import render_worker_caddyfile
from sethlans_worker_agent.config_store import get_data_dir
from shared.caddy_supervisor import CaddySupervisor

try:  # Source mode: shared/ on sys.path.
    from shared.frozen_paths import get_caddy_path
except ImportError:  # pragma: no cover - frozen mode fallback
    from sethlans_worker_agent.frozen_paths import get_caddy_path

logger = logging.getLogger(__name__)

# When set in the process env, swaps templating for a pre-baked
# external Caddyfile (Docker images). Caddy resolves ``{$VAR}``
# placeholders from its process env. Unset on native dev / frozen.
CADDYFILE_PATH_ENV = "SETHLANS_WORKER_CADDYFILE_PATH"

# Mapping from ``render_worker_caddyfile`` kwarg names to the
# ``{$VAR}`` env-var names the Docker Caddyfile references. Used by
# the shared supervisor to overlay Sethlans values onto Caddy's
# spawn env when the external-Caddyfile branch is active.
_WORKER_ENV_OVERLAY = {
    "public_tls_port": "SETHLANS_WORKER_CADDY_PUBLIC_TLS_PORT",
    "loopback_plaintext_port": "SETHLANS_WORKER_CADDY_LOOPBACK_PORT",
    "waitress_upstream_port": "SETHLANS_WORKER_WAITRESS_UPSTREAM_PORT",
    "cert_path": "SETHLANS_WORKER_CERT_PATH",
    "key_path": "SETHLANS_WORKER_KEY_PATH",
}


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
    template_kwargs = {
        "public_tls_port": config.CADDY_PUBLIC_TLS_PORT,
        "loopback_plaintext_port": config.CADDY_LOOPBACK_PORT,
        "waitress_upstream_port": config.WAITRESS_UPSTREAM_PORT,
        "cert_path": cert_path,
        "key_path": key_path,
        "worker_data_dir": data_dir,
    }
    return CaddySupervisor(
        binary_path=binary_path,
        caddyfile_path=caddyfile_path,
        caddyfile_renderer=render_worker_caddyfile,
        template_kwargs=template_kwargs,
        caddyfile_path_env=CADDYFILE_PATH_ENV,
        env_overlay_mapping=_WORKER_ENV_OVERLAY,
    )


def check_caddy_error(supervisor: Optional[CaddySupervisor]) -> bool:
    """Return True if the supervisor's error flag has been raised.

    Used by the agent main loop to detect retry-budget exhaustion.
    When True, the caller is expected to log and exit 1 per spec.
    """
    if supervisor is None:
        return False
    return supervisor.error_event.is_set()
