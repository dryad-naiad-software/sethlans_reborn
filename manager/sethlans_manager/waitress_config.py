# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Waitress tuning + port configuration helpers (Phase 5).

Centralises the env-var > ``manager.ini`` > spec-default hierarchy for
the five Waitress tunables and the two Waitress loopback ports so
``run_manager.py`` stays under the 300-line ceiling.

Tunable defaults come from ``development/specs/waitress-migration-manager.md``
Phase 5 "Waitress tuning" table. Changing any default is a spec-level
change — edit the spec first.
"""

from __future__ import annotations

import configparser
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Spec-fixed defaults (Phase 5) ---------------------------------------
DEFAULT_THREADS = 16
DEFAULT_CHANNEL_TIMEOUT = 300
DEFAULT_CONNECTION_LIMIT = 1000
DEFAULT_MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB

# Public-origin Waitress listener (Caddy's public vhost proxies here).
DEFAULT_WAITRESS_PUBLIC_PORT = 8090
# Internal-origin Waitress listener (tray-helper loopback vhost).
DEFAULT_WAITRESS_INTERNAL_PORT = 8088


def _read_ini(ini_path: Path) -> configparser.ConfigParser:
    """Return a ConfigParser loaded from *ini_path*, empty on failure."""
    cfg = configparser.ConfigParser()
    if not ini_path.exists():
        return cfg
    try:
        cfg.read(ini_path)
    except Exception as exc:
        print(
            f"[WARNING] Could not read manager.ini: {exc}",
            file=sys.stderr,
        )
    return cfg


def _resolve_int(
    env_var: str,
    ini_key: str,
    default: int,
    ini_path: Path,
    *,
    ini_section: str = "server",
) -> int:
    """Resolve an integer tunable per the override hierarchy.

    Precedence: env var > ``manager.ini`` > spec default.
    Non-integer values fall through to the next tier with a warning.
    """
    raw = os.getenv(env_var)
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "%s=%r is not an integer; falling back to ini/default.",
                env_var, raw,
            )

    cfg = _read_ini(ini_path)
    if cfg.has_option(ini_section, ini_key):
        raw = cfg.get(ini_section, ini_key)
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "manager.ini [%s] %s=%r is not an integer; using default.",
                ini_section, ini_key, raw,
            )

    return default


def get_waitress_tuning(ini_path: Path) -> dict:
    """Return a dict of Waitress ``create_server`` tuning kwargs.

    Keys match the Waitress public API: ``threads``, ``channel_timeout``,
    ``connection_limit``, ``max_request_body_size``.
    """
    return {
        "threads": _resolve_int(
            "SETHLANS_MANAGER_WAITRESS_THREADS",
            "waitress_threads",
            DEFAULT_THREADS,
            ini_path,
        ),
        "channel_timeout": _resolve_int(
            "SETHLANS_MANAGER_WAITRESS_CHANNEL_TIMEOUT",
            "waitress_channel_timeout",
            DEFAULT_CHANNEL_TIMEOUT,
            ini_path,
        ),
        "connection_limit": _resolve_int(
            "SETHLANS_MANAGER_WAITRESS_CONNECTION_LIMIT",
            "waitress_connection_limit",
            DEFAULT_CONNECTION_LIMIT,
            ini_path,
        ),
        "max_request_body_size": _resolve_int(
            "SETHLANS_MANAGER_WAITRESS_MAX_REQUEST_BODY_SIZE",
            "waitress_max_request_body_size",
            DEFAULT_MAX_REQUEST_BODY_SIZE,
            ini_path,
        ),
    }


def get_waitress_public_port(ini_path: Path) -> int:
    """Resolve the public-origin Waitress listener port.

    Hierarchy: ``SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC`` >
    ``[server] waitress_loopback_port_public`` > ``8090``.
    """
    return _resolve_int(
        "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC",
        "waitress_loopback_port_public",
        DEFAULT_WAITRESS_PUBLIC_PORT,
        ini_path,
    )


def _try_env_int(name: str) -> Optional[int]:
    """Parse an env var as int; return ``None`` on missing/invalid."""
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not an integer; falling back to next tier.",
            name, raw,
        )
        return None


def _try_ini_int(cfg, section: str, key: str) -> Optional[int]:
    """Parse an INI key as int; return ``None`` on missing/invalid."""
    if not cfg.has_option(section, key):
        return None
    raw = cfg.get(section, key)
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "manager.ini [%s] %s=%r is not an integer; falling back.",
            section, key, raw,
        )
        return None


def get_waitress_internal_port(ini_path: Path) -> int:
    """Resolve the internal-origin Waitress listener port.

    Hierarchy: ``SETHLANS_MANAGER_LOOPBACK_PORT`` (legacy, Phase 2) >
    ``SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL`` >
    ``[server] waitress_loopback_port_internal`` >
    ``[server] loopback_port`` (legacy) > ``8088``.

    Two aliases are honoured on each tier for backwards compatibility
    with Phase 2 configs that named the loopback listener
    ``loopback_port`` / ``SETHLANS_MANAGER_LOOPBACK_PORT``.
    """
    for env_name in (
        "SETHLANS_MANAGER_LOOPBACK_PORT",
        "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL",
    ):
        value = _try_env_int(env_name)
        if value is not None:
            return value

    cfg = _read_ini(ini_path)
    for ini_key in (
        "waitress_loopback_port_internal",
        "loopback_port",
    ):
        value = _try_ini_int(cfg, "server", ini_key)
        if value is not None:
            return value

    return DEFAULT_WAITRESS_INTERNAL_PORT


def resolve_ini_path(manager_dir: Optional[Path] = None) -> Path:
    """Return the manager.ini path (data dir when frozen)."""
    # Import lazily so this module stays importable during test discovery
    # without Django or the frozen-paths shim loaded.
    from shared.frozen_paths import get_data_dir, is_frozen

    if is_frozen():
        return get_data_dir("manager") / "manager.ini"
    if manager_dir is not None:
        return Path(manager_dir) / "manager.ini"
    raise ValueError(
        "resolve_ini_path(manager_dir=...) is required outside frozen mode."
    )


# ---------------------------------------------------------------------
# Settings-module helpers (called from sethlans_manager.settings).
# ---------------------------------------------------------------------

def resolve_internal_port_for_settings(
    config, legacy_env_precedence: bool = True,
) -> int:
    """Resolve the internal-origin port from ``settings.py`` context.

    ``config`` is the already-loaded ``ConfigParser`` the settings
    module uses. Kept as a free function so ``settings.py`` stays thin
    — the full override hierarchy lives here.
    """
    if legacy_env_precedence:
        legacy = os.getenv("SETHLANS_MANAGER_LOOPBACK_PORT")
        if legacy is not None:
            return int(legacy)
    raw = os.getenv("SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL")
    if raw is not None:
        return int(raw)
    if config.has_option("server", "waitress_loopback_port_internal"):
        return int(config.get(
            "server", "waitress_loopback_port_internal",
        ))
    if config.has_option("server", "loopback_port"):
        return int(config.get("server", "loopback_port"))
    return DEFAULT_WAITRESS_INTERNAL_PORT


def resolve_public_port_for_settings(config) -> Optional[int]:
    """Resolve the public-origin port from ``settings.py`` context.

    Returns ``None`` when neither the env var nor ``manager.ini``
    supplies an explicit value. This tolerance path preserves the
    Phase 2 middleware contract: ``UrlconfOriginMiddleware`` with
    ``WAITRESS_LOOPBACK_PORT_PUBLIC=None`` treats any non-internal
    ``SERVER_PORT`` as "public" and lets the request through. It keeps
    the Django test client (which defaults to ``SERVER_PORT=80``)
    working without a per-test override.

    ``run_manager.py`` does NOT consult this resolver — it calls
    :func:`get_waitress_public_port` which falls through to the spec
    default of 8090. Production serving therefore always binds to a
    concrete port even when this resolver returns ``None``.
    """
    raw = os.getenv("SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC")
    if raw is not None:
        return int(raw)
    if config.has_option("server", "waitress_loopback_port_public"):
        return int(config.get(
            "server", "waitress_loopback_port_public",
        ))
    return None
