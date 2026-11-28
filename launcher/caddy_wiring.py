# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The manager's Caddy, as the launcher runs it (D3, FR-LN-3, FR-SF-5).

One Caddy fronts setup mode and then the running manager, on the
manager's certificate:

* :func:`manager_tls_paths`: the manager's self-signed pair, in the
  folder the manager itself uses, from
  :func:`sethlans_manager.tls_setup.get_tls_dir` (the settings folder's
  ``manager/tls`` when installed; from source ``manager/tls`` or
  ``SETHLANS_MANAGER_DATA_DIR``; ``SETHLANS_TLS_DATA_DIR`` always wins;
  issue #346). The launcher makes the pair when it is missing, before
  Caddy starts. A bring-your-own certificate (``[tls] cert_file`` /
  ``key_file``) is not served by this Caddy yet: issue #347.
* :func:`start_or_retarget`: start Caddy on a public port, or restart it
  when the port or anything else in its Caddyfile changed; a no-op when
  it already runs with the same Caddyfile.
* :func:`stop_caddy`: stop it (worker-only mode, Reset, quit).

Ports other than the public one come from the settings file
(``sethlans_manager.waitress_config``). The Caddyfile is
``<manager identity folder>/caddy/Caddyfile``. The supervisor is
registered with :mod:`launcher.supervision` so the signal handler stops
it too. Called from the launcher's loop thread only.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from launcher import supervision
from launcher.setup_helpers import CADDY_LOOPBACK_PORT, CADDY_PUBLIC_TLS_PORT
from shared import frozen_paths, settings_file

logger = logging.getLogger(__name__)

# The template kwargs Caddy runs with now (None: not started by us).
_current_kwargs: Optional[dict] = None


def _ensure_manager_on_syspath() -> None:
    """Add ``manager/`` to ``sys.path`` (source mode) for ``sethlans_manager.*``."""
    manager_dir = Path(__file__).resolve().parent.parent / "manager"
    if manager_dir.is_dir() and str(manager_dir) not in sys.path:
        sys.path.insert(0, str(manager_dir))


def manager_tls_paths() -> tuple[Path, Path]:
    """``(cert, key)`` of the manager's self-signed pair, where the manager looks.

    Only the self-signed pair: a bring-your-own certificate is issue #347.
    """
    _ensure_manager_on_syspath()
    from sethlans_manager.tls_setup import get_tls_dir

    manager_dir = frozen_paths.get_manager_dir()
    tls_dir = get_tls_dir(False, manager_dir, manager_dir.parent)
    return tls_dir / "cert.pem", tls_dir / "key.pem"


def ensure_manager_tls_cert() -> tuple[Path, Path]:
    """Make the manager's pair when ``cert.pem`` is missing; return ``(cert, key)``."""
    from shared.cert_utils import generate_self_signed_cert

    cert_path, key_path = manager_tls_paths()
    if not cert_path.exists():
        generate_self_signed_cert(cert_path, key_path)
        logger.info("Made the manager TLS certificate at %s", cert_path)
    return cert_path, key_path


def configured_public_port(ini_path=None) -> int:
    """``[server] port`` (``setup_port.earlier_port``, the one reader), else 8080."""
    from launcher.setup_port import earlier_port

    return earlier_port(ini_path) or CADDY_PUBLIC_TLS_PORT


def loopback_ports(ini_path=None) -> tuple[int, int, int]:
    """Our own loopback ports: Caddy's, Waitress public, Waitress internal."""
    _ensure_manager_on_syspath()
    from sethlans_manager.waitress_config import get_waitress_internal_port, get_waitress_public_port

    ini_path = settings_file.settings_path() if ini_path is None else Path(ini_path)
    return CADDY_LOOPBACK_PORT, get_waitress_public_port(ini_path), get_waitress_internal_port(ini_path)


def _template_kwargs(public_port: int) -> dict:
    cert_path, key_path = ensure_manager_tls_cert()
    loopback, waitress_public, waitress_internal = loopback_ports()
    return {
        "public_tls_port": int(public_port),
        "loopback_plaintext_port": loopback,
        "waitress_public_port": waitress_public,
        "waitress_internal_port": waitress_internal,
        "cert_path": cert_path,
        "key_path": key_path,
        # The template checks that cert and key lie inside this folder.
        "manager_data_dir": cert_path.parent,
    }


def _caddyfile_path() -> Path:
    path = frozen_paths.get_identity_dir("manager") / "caddy" / "Caddyfile"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _launch(supervisor, kwargs: dict) -> None:
    from launcher.caddy_launcher import build_manager_caddy_supervisor

    if supervisor is None:
        supervisor = build_manager_caddy_supervisor(caddyfile_path=_caddyfile_path(), **kwargs)
        supervision.set_caddy_supervisor(supervisor)
        supervisor.start()
    else:
        supervisor.update_template_kwargs(**kwargs)
        supervisor.restart()


def start_or_retarget(port: int) -> None:
    """Run Caddy with public port ``port``; restart only when its Caddyfile changes.

    Raises when Caddy cannot start: a manager without its public TLS
    listener cannot be reached.
    """
    global _current_kwargs
    kwargs = _template_kwargs(port)
    supervisor = supervision.get_caddy_supervisor()
    if supervisor is not None and supervisor.is_running() and kwargs == _current_kwargs:
        return
    _current_kwargs = None
    try:
        _launch(supervisor, kwargs)
    except Exception as exc:
        logger.exception("Failed to start Caddy")
        print(f"\n[ERROR] Could not start Caddy on port {port}: {exc}\n", file=sys.stderr)
        raise
    _current_kwargs = kwargs
    logger.info("Caddy serves port %d", port)


def stop_caddy(timeout: float = 5.0) -> None:
    """Stop the manager's Caddy if we run one; idempotent."""
    global _current_kwargs
    _current_kwargs = None
    supervisor = supervision.take_caddy_supervisor()
    if supervisor is None:
        return
    try:
        supervisor.stop(timeout=timeout)
    except Exception:
        logger.exception("Error stopping Caddy")


def start_caddy_supervisor(manager_data=None) -> None:
    """The current loop's entry (``orchestration``), until chunk 6b replaces it."""
    del manager_data  # the TLS folder comes from manager_tls_paths()
    start_or_retarget(configured_public_port())


def _reset_for_tests() -> None:
    global _current_kwargs
    _current_kwargs = None
