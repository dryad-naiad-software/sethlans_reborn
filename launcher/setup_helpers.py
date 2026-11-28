# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Port constants and port finders for the bootstrap launcher.

The setup token lives in ``launcher.setup_state`` and the setup port in
``launcher.setup_port``. No Django dependency — stdlib only.
"""

import socket

# Port range for auto-detection.
MANAGER_PORT = 8080
MANAGER_PORT_RANGE_END = 8099

# Caddy public TLS listener default. Shares the same numeric port
# workers historically used; launcher keeps the on-the-wire contract
# identical when setup is complete (Caddy terminates TLS in front of
# Waitress, but the handshake cert bytes match).
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


def find_available_port(start: int = MANAGER_PORT) -> int:
    """Find an available port via trial ``socket.bind()``.

    Tries ports from *start* through ``MANAGER_PORT_RANGE_END``.
    Returns the first available port, or *start* if none are free
    (best-effort; Waitress will handle the actual bind).
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
