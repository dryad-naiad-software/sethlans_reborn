# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-mode helpers for the bootstrap launcher.

Extracted from ``run_launcher.py`` to keep it under the 300-line limit.
These functions handle port detection, setup token management, atomic
config writes, and Caddy setup-restart IPC (manager spec Phase 3).
No Django dependency — stdlib only.
"""

import configparser
import json
import logging
import os
import secrets
import socket
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Port range for auto-detection.
MANAGER_PORT = 8080
MANAGER_PORT_RANGE_END = 8099

# Caddy public TLS listener default. Shares the same numeric port
# workers historically used; launcher keeps the on-the-wire contract
# identical when setup is complete (Caddy terminates instead of
# uvicorn, but the handshake cert bytes match).
CADDY_PUBLIC_TLS_PORT = 8080
# Caddy loopback plaintext listener default. Must not overlap with
# the Waitress loopback listener (port 8088 — spec Phase 2).
CADDY_LOOPBACK_PORT = 8089
CADDY_LOOPBACK_RANGE_END = 8099

# Waitress public-origin listener default (spec Phase 5). Caddy's
# public vhost proxies here; must not overlap with
# ``CADDY_LOOPBACK_PORT`` or ``WAITRESS_LOOPBACK_PORT_INTERNAL``.
WAITRESS_PUBLIC_PORT = 8090
WAITRESS_PUBLIC_RANGE_END = 8099

# Filename for the Django → launcher IPC used to trigger Caddy
# restarts after a wizard network-step port change (manager spec
# Phase 3 setup serialization / IPC protocol).
SETUP_RESTART_REQUEST_FILENAME = "setup_restart_request.json"


def find_available_port(start: int = MANAGER_PORT) -> int:
    """Find an available port via trial ``socket.bind()``.

    Tries ports from *start* through ``MANAGER_PORT_RANGE_END``.
    Returns the first available port, or *start* if none are free
    (best-effort; uvicorn will handle the actual bind).
    """
    for port in range(start, MANAGER_PORT_RANGE_END + 1):
        try:
            with socket.socket(
                socket.AF_INET, socket.SOCK_STREAM,
            ) as sock:
                sock.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
    return start


def find_available_loopback_port(
    start: int = CADDY_LOOPBACK_PORT,
    end: int = CADDY_LOOPBACK_RANGE_END,
) -> int:
    """Find an available loopback-only port (127.0.0.1 bind).

    Used for the Caddy loopback plaintext listener. Returns *start*
    on best-effort failure — the caller surfaces the bind error.
    """
    for port in range(start, end + 1):
        try:
            with socket.socket(
                socket.AF_INET, socket.SOCK_STREAM,
            ) as sock:
                sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    return start


def find_available_waitress_public_port(
    start: int = WAITRESS_PUBLIC_PORT,
    end: int = WAITRESS_PUBLIC_RANGE_END,
) -> int:
    """Find an available loopback-only port for Waitress (public origin).

    Spec Phase 5: the public-origin Waitress listener binds
    ``127.0.0.1:<port>``; Caddy's public TLS vhost reverse-proxies
    here. Range defaults to 8090..8099 so it never collides with the
    Caddy loopback (``8089``) or the Waitress internal loopback
    (``8088``).
    """
    for port in range(start, end + 1):
        try:
            with socket.socket(
                socket.AF_INET, socket.SOCK_STREAM,
            ) as sock:
                sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    return start


def generate_setup_token(manager_data: Path) -> str:
    """Return the existing setup token, or generate a new one.

    Reuses any existing non-empty ``[setup] token`` value in
    ``manager.ini`` so an in-progress wizard session survives
    launcher restarts.  Only generates and writes a new token when
    the section is missing or the value is empty. Returns the token.
    """
    ini_path = manager_data / "manager.ini"

    config = configparser.ConfigParser()
    if ini_path.exists():
        config.read(ini_path)

    if config.has_section("setup"):
        existing = config.get("setup", "token", fallback="")
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    if not config.has_section("setup"):
        config.add_section("setup")
    config.set("setup", "token", token)

    atomic_write_ini(config, ini_path)
    return token


def remove_setup_section(manager_data: Path) -> None:
    """Remove the ``[setup]`` section from manager.ini."""
    ini_path = manager_data / "manager.ini"
    if not ini_path.exists():
        return

    config = configparser.ConfigParser()
    config.read(ini_path)

    if config.has_section("setup"):
        config.remove_section("setup")
        atomic_write_ini(config, ini_path)


def atomic_write_ini(
    config: configparser.ConfigParser, path: Path,
) -> None:
    """Write a ConfigParser to *path* atomically."""
    parent = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".ini")
    try:
        with os.fdopen(fd, "w") as f:
            config.write(f)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically write ``content`` to ``path``.

    Generalised form of :func:`atomic_write_ini` for non-ini
    payloads (Caddyfile, setup_restart_request.json). Creates the
    parent directory if missing. On write failure, best-effort
    unlinks the tempfile so no ``.tmp`` crumbs leak.

    Note: manager-side Caddyfile writes should prefer the shared
    helper at :func:`shared.caddy_supervisor.io.atomic_write_text`
    which has the same semantics; this helper exists so launcher
    code needn't import from the shared package just for an atomic
    write (and for the setup_restart_request.json use case which is
    not Caddyfile-specific).
    """
    if content == "":
        raise ValueError("content must not be empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = str(path.parent)
    fd, tmp_name = tempfile.mkstemp(
        dir=parent, prefix=".setup_", suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def setup_restart_request_path(manager_data: Path) -> Path:
    """Return the IPC file path for wizard-triggered Caddy restarts."""
    return manager_data / SETUP_RESTART_REQUEST_FILENAME


def write_setup_restart_request(
    manager_data: Path, payload: dict,
) -> None:
    """Atomically write the Caddy-restart IPC request file.

    Called from the Django side (mutating setup endpoint) while
    holding ``setup_mutation_lock``. The launcher polls the target
    path every 250 ms and, on detecting it, triggers a Caddy
    restart with the new config values. Payload shape is caller-
    defined; the launcher treats it as opaque state to hand back
    to the Caddyfile renderer.
    """
    target = setup_restart_request_path(manager_data)
    atomic_write_text(target, json.dumps(payload))


def read_setup_restart_request(
    manager_data: Path,
) -> Optional[dict]:
    """Return the pending Caddy-restart request, or ``None``.

    Returns ``None`` if the file is missing or unparseable. Parse
    failures are logged but do not raise — the launcher's poll loop
    must tolerate transient write-in-progress states.
    """
    target = setup_restart_request_path(manager_data)
    if not target.exists():
        return None
    try:
        with open(target, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.exception(
            "Failed to read setup-restart request at %s", target,
        )
        return None


def clear_setup_restart_request(manager_data: Path) -> None:
    """Delete the Caddy-restart request file (post-restart cleanup).

    Best-effort: silently tolerates a missing file (another process
    may have reaped it) but logs other OS errors for diagnosis.
    """
    target = setup_restart_request_path(manager_data)
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.exception(
            "Failed to clear setup-restart request at %s", target,
        )
