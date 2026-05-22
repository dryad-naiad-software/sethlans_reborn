# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher-side Caddy supervisor bootstrap (manager spec Phase 3).

Split out of :mod:`launcher.orchestration` to keep that module under
the 300-line ceiling. This module owns:

* ``manager/`` sys.path insertion for source mode (so the
  ``sethlans_manager.caddy_template`` renderer is importable before
  :func:`launcher.caddy_launcher.build_manager_caddy_supervisor`
  resolves it).
* Reading ports from ``manager.ini`` with sensible defaults.
* Constructing + registering + starting the supervisor, aborting
  startup on failure (a manager without a public TLS listener is
  unusable).

Shutdown is handled automatically by
:func:`launcher.supervision.shutdown_supervisors`.
"""

from __future__ import annotations

import configparser
import logging
import sys
from pathlib import Path

from launcher import supervision
from launcher.setup_helpers import (
    CADDY_LOOPBACK_PORT,
    CADDY_PUBLIC_TLS_PORT,
)

logger = logging.getLogger(__name__)


def ensure_manager_tls_cert(manager_data: Path) -> tuple[Path, Path]:
    """Pre-generate manager TLS cert if absent; return ``(cert, key)``.

    Mirrors the wizard pattern (see :mod:`launcher.wizard_caddy_lifecycle`):
    the launcher creates ``<manager_data>/tls/{cert,key}.pem`` before Caddy
    starts so the supervisor finds the files at boot. The manager runtime's
    ``tls_setup.py`` is idempotent and will reuse these files unchanged
    on subsequent boots (FR-CERT2 of issue #202).
    """
    from shared.cert_utils import generate_self_signed_cert
    tls_dir = manager_data / "tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_path = tls_dir / "cert.pem"
    key_path = tls_dir / "key.pem"
    if not cert_path.exists():
        generate_self_signed_cert(cert_path, key_path)
        logger.info("Pre-generated manager TLS cert at %s", cert_path)
    return cert_path, key_path


def _ensure_manager_on_syspath() -> None:
    """Add ``manager/`` to ``sys.path`` so ``sethlans_manager.*`` imports work.

    ``build_manager_caddy_supervisor`` eagerly loads
    ``sethlans_manager.caddy_template`` via ``_load_manager_renderer``.
    In source mode, ``manager/`` is not on ``sys.path`` unless a caller
    has added it. Mirrors the pattern in
    :func:`launcher.broadcaster_supervisor._load_multicast_broadcaster_cls`.
    """
    project_root = Path(__file__).resolve().parent.parent
    manager_dir = project_root / "manager"
    if manager_dir.is_dir() and str(manager_dir) not in sys.path:
        sys.path.insert(0, str(manager_dir))


def _resolve_caddy_public_port(ini_path: Path) -> int:
    """Return ``[server] port`` from manager.ini or the spec default."""
    cfg = configparser.ConfigParser()
    if ini_path.exists():
        try:
            cfg.read(ini_path)
        except (configparser.Error, OSError):
            return CADDY_PUBLIC_TLS_PORT
    if cfg.has_option("server", "port"):
        try:
            return int(cfg.get("server", "port"))
        except ValueError:
            pass
    return CADDY_PUBLIC_TLS_PORT


def prepare_manager_caddy_and_resolve_port(manager_data: Path) -> int:
    """One-shot helper for the wizard hand-off path (issue #202).

    Bundles the three steps the launcher must take before health-probing
    the manager runtime: pre-generate the TLS cert, start the Caddy
    supervisor, and resolve the public TLS port from ``manager.ini``.

    Keeps :func:`launcher.wizard_runtime.hand_off_to_runtime` under the
    300-line ceiling (NF-1) by hosting the bundled logic here, beside
    the Caddy bootstrap it wraps. Returns the resolved port so the
    caller can pass it to ``wait_for_runtime_port_bind``.
    """
    ensure_manager_tls_cert(manager_data)
    start_caddy_supervisor(manager_data)
    return _resolve_caddy_public_port(manager_data / "manager.ini")


def start_caddy_supervisor(manager_data: Path) -> None:
    """Build + register + start a Caddy supervisor for the manager.

    Aborts startup with a stderr banner if ``supervisor.start()``
    raises — a setup wizard / dashboard without a public TLS listener
    is unusable.
    """
    _ensure_manager_on_syspath()
    # Late import: manager/ must be on sys.path first.
    from launcher.caddy_launcher import build_manager_caddy_supervisor
    from sethlans_manager.waitress_config import (
        get_waitress_internal_port,
        get_waitress_public_port,
    )

    ini_path = manager_data / "manager.ini"
    caddyfile_path = manager_data / "caddy" / "Caddyfile"
    caddyfile_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path = manager_data / "tls" / "cert.pem"
    key_path = manager_data / "tls" / "key.pem"

    public_tls_port = _resolve_caddy_public_port(ini_path)
    waitress_public_port = get_waitress_public_port(ini_path)
    waitress_internal_port = get_waitress_internal_port(ini_path)

    supervisor = build_manager_caddy_supervisor(
        caddyfile_path=caddyfile_path,
        public_tls_port=public_tls_port,
        loopback_plaintext_port=CADDY_LOOPBACK_PORT,
        cert_path=cert_path,
        key_path=key_path,
        manager_data_dir=manager_data,
        waitress_public_port=waitress_public_port,
        waitress_internal_port=waitress_internal_port,
    )
    supervision.set_caddy_supervisor(supervisor)
    try:
        supervisor.start()
    except Exception as exc:
        logger.exception("Failed to start Caddy supervisor")
        print(
            "\n[ERROR] Could not start Caddy reverse proxy: "
            f"{exc}\n"
            "Manager would only listen on loopback; aborting startup.\n",
            file=sys.stderr,
        )
        raise
