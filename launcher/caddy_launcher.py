# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Launcher-side Caddy supervisor driver (manager spec Phase 3).

Thin adapter: wires the shared :class:`shared.caddy_supervisor.CaddySupervisor`
to the manager Caddyfile template and the manager's env-var overlay.
Mirrors ``worker/sethlans_worker_agent/agent_caddy.py`` in shape.

Runtime flow:

1. Launcher resolves ports + cert paths (from ``manager.ini`` +
   defaults in :mod:`launcher.setup_helpers`) before starting any
   children.
2. :func:`build_manager_caddy_supervisor` constructs the supervisor;
   :meth:`CaddySupervisor.start` templates the Caddyfile (native) or
   uses ``$SETHLANS_MANAGER_CADDYFILE_PATH`` verbatim (Docker).
3. Launcher polls the setup-restart-request IPC file (see
   :mod:`launcher.setup_helpers`). When a wizard network-step change
   lands, :func:`apply_restart_request` updates the supervisor's
   template kwargs and triggers :meth:`CaddySupervisor.restart`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping, Optional

from shared.caddy_supervisor import CaddySupervisor

try:  # Source mode.
    from shared.frozen_paths import get_caddy_path
except ImportError:  # pragma: no cover - defensive
    from sethlans_manager.frozen_paths import get_caddy_path

logger = logging.getLogger(__name__)

# Env var whose presence switches the supervisor from templating a
# Caddyfile to using a pre-baked one (Docker images).
CADDYFILE_PATH_ENV = "SETHLANS_MANAGER_CADDYFILE_PATH"

# Mapping from manager ``render_manager_caddyfile`` kwarg names to the
# ``{$VAR}`` env-var names in the Docker manager Caddyfile. Used by
# the shared supervisor to overlay values onto Caddy's spawn env in
# the external-Caddyfile branch.
_MANAGER_ENV_OVERLAY = {
    "public_tls_port": "SETHLANS_MANAGER_CADDY_PUBLIC_TLS_PORT",
    "loopback_plaintext_port": "SETHLANS_MANAGER_CADDY_LOOPBACK_PORT",
    # Phase 5 canonical kwargs.
    "waitress_public_port":
        "SETHLANS_MANAGER_WAITRESS_PUBLIC_PORT",
    "waitress_internal_port":
        "SETHLANS_MANAGER_WAITRESS_LOOPBACK_PORT",
    # Legacy aliases — preserved so Docker deployments that still set
    # ``SETHLANS_MANAGER_UVICORN_UPSTREAM_PORT`` keep working during
    # the migration window. Phase 7 deletes these along with the
    # uvicorn dependency.
    "uvicorn_upstream_port": "SETHLANS_MANAGER_UVICORN_UPSTREAM_PORT",
    "waitress_loopback_upstream_port":
        "SETHLANS_MANAGER_WAITRESS_LOOPBACK_PORT",
    "cert_path": "SETHLANS_MANAGER_CERT_PATH",
    "key_path": "SETHLANS_MANAGER_KEY_PATH",
}


def _load_manager_renderer():
    """Import :func:`render_manager_caddyfile` with source tolerance.

    The manager module lives under ``manager/sethlans_manager/``;
    the launcher doesn't otherwise import from that namespace. The
    broadcaster supervisor already fixes ``sys.path`` to include
    ``manager/`` at launcher boot, so a plain import works once that
    has run. This helper is a small abstraction so callers get a
    clear import error message if the path hasn't been wired yet.
    """
    try:
        from sethlans_manager.caddy_template import render_manager_caddyfile
    except ImportError as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            "sethlans_manager.caddy_template not importable; the "
            "launcher must add manager/ to sys.path before calling "
            "build_manager_caddy_supervisor()"
        ) from exc
    return render_manager_caddyfile


def build_manager_caddy_supervisor(
    *,
    caddyfile_path: Path,
    public_tls_port: int,
    loopback_plaintext_port: int,
    cert_path: Path,
    key_path: Path,
    manager_data_dir: Path,
    waitress_public_port: Optional[int] = None,
    waitress_internal_port: Optional[int] = None,
    uvicorn_upstream_port: Optional[int] = None,
    waitress_loopback_upstream_port: Optional[int] = None,
    binary_path: Optional[Path] = None,
) -> CaddySupervisor:
    """Construct a manager-flavoured :class:`CaddySupervisor`.

    Caller owns the supervisor's lifecycle (``start`` / ``stop`` /
    ``restart`` / ``error_event`` polling). This helper assembles
    the constructor args from launcher-side configuration.

    Prefer ``waitress_public_port`` / ``waitress_internal_port`` for
    new callers (spec Phase 5). The legacy ``uvicorn_upstream_port``
    / ``waitress_loopback_upstream_port`` kwargs are accepted as
    aliases for the Phase 3 call sites that have not been updated yet.
    """
    resolved_binary = Path(binary_path) if binary_path else Path(
        get_caddy_path(),
    )
    public_upstream = (
        waitress_public_port
        if waitress_public_port is not None
        else uvicorn_upstream_port
    )
    internal_upstream = (
        waitress_internal_port
        if waitress_internal_port is not None
        else waitress_loopback_upstream_port
    )
    if public_upstream is None or internal_upstream is None:
        raise ValueError(
            "build_manager_caddy_supervisor requires both upstream "
            "ports; pass waitress_public_port and "
            "waitress_internal_port (or the legacy "
            "uvicorn_upstream_port / waitress_loopback_upstream_port "
            "aliases)."
        )
    template_kwargs = {
        "public_tls_port": public_tls_port,
        "loopback_plaintext_port": loopback_plaintext_port,
        "waitress_public_port": public_upstream,
        "waitress_internal_port": internal_upstream,
        "cert_path": cert_path,
        "key_path": key_path,
        "manager_data_dir": manager_data_dir,
    }
    return CaddySupervisor(
        binary_path=resolved_binary,
        caddyfile_path=caddyfile_path,
        caddyfile_renderer=_load_manager_renderer(),
        template_kwargs=template_kwargs,
        caddyfile_path_env=CADDYFILE_PATH_ENV,
        env_overlay_mapping=_MANAGER_ENV_OVERLAY,
    )


def apply_restart_request(
    supervisor: CaddySupervisor,
    request: Mapping[str, object],
) -> None:
    """Update template kwargs from an IPC request and restart Caddy.

    ``request`` is the decoded JSON written by the Django side into
    ``setup_restart_request.json`` while holding the setup mutation
    lock. Only keys present in the request override the current
    template kwargs — missing keys keep their last-known-good value.
    The launcher-side caller is responsible for clearing the request
    file after this function returns.
    """
    supervisor.update_template_kwargs(**dict(request))
    logger.info(
        "Applying wizard-triggered Caddy restart (keys: %s)",
        list(request.keys()),
    )
    supervisor.restart()
