# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher-side wizard Caddy supervisor bootstrap (issue #170).

Mirrors :mod:`launcher.caddy_wiring` for the wizard channel: builds and
starts a :class:`shared.caddy_supervisor.CaddySupervisor` that fronts
the wizard subprocess with TLS termination. The wizard subprocess
listens plain HTTP on a loopback port; Caddy is the only LAN-reachable
listener.

Lifetime is owned by :mod:`launcher.wizard_orchestration`:

1. :func:`start_wizard_caddy_supervisor` is called *after* the wizard
   subprocess has bound its loopback port (so the supervisor knows
   the upstream port to proxy to).
2. The returned supervisor handle is stashed by the orchestration
   module so the cleanup path can stop it before the launcher exits.
3. ``stop()`` is called from the wizard cleanup path (terminate
   wizard subprocess first, then Caddy — reverse of startup).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from shared.caddy_supervisor import CaddySupervisor

try:  # Source mode.
    from shared.frozen_paths import get_caddy_path
except ImportError:  # pragma: no cover - defensive
    from sethlans_manager.frozen_paths import get_caddy_path

logger = logging.getLogger(__name__)

# Wizard Caddy public TLS port (issue #170). Pinned at 8100 so it
# matches the URL the launcher's cold-boot probe + tray "Open Setup
# Wizard" already point at; the wizard subprocess moved to the
# 8099 / 8101..8104 loopback range to free this port.
WIZARD_PUBLIC_TLS_PORT = 8100

# Env var whose presence switches the supervisor from templating a
# Caddyfile to using a pre-baked one (Docker images / advanced users).
# Distinct from the manager / worker env vars so the three Caddy
# instances never collide on the same image.
WIZARD_CADDYFILE_PATH_ENV = "SETHLANS_WIZARD_CADDYFILE_PATH"

# Mapping from ``render_wizard_caddyfile`` kwarg names to env-var names
# used to overlay values onto Caddy's spawn env in the external-Caddyfile
# branch. Mirrors the manager / worker overlays so the three Caddyfile
# templates share a substitution convention.
_WIZARD_ENV_OVERLAY = {
    "public_tls_port": "SETHLANS_WIZARD_CADDY_PUBLIC_TLS_PORT",
    "loopback_port": "SETHLANS_WIZARD_LOOPBACK_PORT",
    "cert_path": "SETHLANS_WIZARD_CERT_PATH",
    "key_path": "SETHLANS_WIZARD_KEY_PATH",
}


def _ensure_wizard_on_syspath() -> None:
    """Add ``wizard/`` to ``sys.path`` so ``sethlans_wizard.*`` imports work.

    The launcher doesn't otherwise import from the wizard namespace; in
    source mode the wizard directory is added by ``run_wizard.py`` only
    once it boots its own process. Mirrors the manager-side helper in
    :mod:`launcher.caddy_wiring`.
    """
    project_root = Path(__file__).resolve().parent.parent
    wizard_dir = project_root / "wizard"
    if wizard_dir.is_dir() and str(wizard_dir) not in sys.path:
        sys.path.insert(0, str(wizard_dir))


def _load_wizard_renderer():
    """Import :func:`render_wizard_caddyfile`."""
    _ensure_wizard_on_syspath()
    try:
        from sethlans_wizard.caddy_template import (
            render_wizard_caddyfile,
        )
    except ImportError as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            "sethlans_wizard.caddy_template not importable; the "
            "launcher must add wizard/ to sys.path before calling "
            "start_wizard_caddy_supervisor()"
        ) from exc
    return render_wizard_caddyfile


def build_wizard_caddy_supervisor(
    *,
    caddyfile_path: Path,
    public_tls_port: int,
    loopback_port: int,
    cert_path: Path,
    key_path: Path,
    wizard_data_dir: Path,
    binary_path: Optional[Path] = None,
) -> CaddySupervisor:
    """Construct a wizard-flavoured :class:`CaddySupervisor`.

    Caller owns the supervisor's lifecycle (``start`` / ``stop`` /
    ``error_event`` polling). This helper assembles the constructor
    args from launcher-side configuration; it does not call
    :meth:`CaddySupervisor.start`.
    """
    resolved_binary = Path(binary_path) if binary_path else Path(
        get_caddy_path(),
    )
    template_kwargs = {
        "public_tls_port": public_tls_port,
        "loopback_port": loopback_port,
        "cert_path": cert_path,
        "key_path": key_path,
        "wizard_data_dir": wizard_data_dir,
    }
    return CaddySupervisor(
        binary_path=resolved_binary,
        caddyfile_path=caddyfile_path,
        caddyfile_renderer=_load_wizard_renderer(),
        template_kwargs=template_kwargs,
        caddyfile_path_env=WIZARD_CADDYFILE_PATH_ENV,
        env_overlay_mapping=_WIZARD_ENV_OVERLAY,
    )


def start_wizard_caddy_supervisor(
    data_dir: Path,
    wizard_loopback_port: int,
) -> CaddySupervisor:
    """Build + start a Caddy supervisor for the wizard. Returns the handle.

    The launcher's :func:`launcher.wizard_orchestration.run_wizard_mode`
    calls this AFTER the wizard subprocess writes its loopback port file
    (so the upstream port is known) and BEFORE the launcher's
    ``wait_for_health`` probe (so the public TLS URL is reachable when
    the probe fires).

    ``data_dir`` is the per-user shared data dir (the parent of
    ``<data_dir>/wizard/``). The cert + key MUST already exist at
    ``<data_dir>/wizard/tls/{cert.pem,key.pem}`` — the launcher's
    cert-generation step (``shared.cert_utils.generate_self_signed_cert``)
    is responsible for that.
    """
    wizard_subdir = data_dir / "wizard"
    caddyfile_path = wizard_subdir / "Caddyfile"
    cert_path = wizard_subdir / "tls" / "cert.pem"
    key_path = wizard_subdir / "tls" / "key.pem"

    supervisor = build_wizard_caddy_supervisor(
        caddyfile_path=caddyfile_path,
        public_tls_port=WIZARD_PUBLIC_TLS_PORT,
        loopback_port=wizard_loopback_port,
        cert_path=cert_path,
        key_path=key_path,
        wizard_data_dir=wizard_subdir,
    )
    try:
        supervisor.start()
    except Exception as exc:
        logger.exception("Failed to start wizard Caddy supervisor")
        print(
            "\n[ERROR] Could not start wizard Caddy reverse proxy: "
            f"{exc}\n"
            "Wizard would not be reachable; aborting first-run flow.\n",
            file=sys.stderr,
        )
        raise
    logger.info(
        "Wizard Caddy supervisor started "
        "(public=:%d, loopback=127.0.0.1:%d)",
        WIZARD_PUBLIC_TLS_PORT, wizard_loopback_port,
    )
    return supervisor
