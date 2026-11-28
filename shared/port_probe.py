# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Is a TCP port free to listen on? One exclusive probe (stdlib only, issue #350).

Setup mode (``sethlans_manager.setup_mode.net``) and the launcher's setup
port (``launcher.setup_port``) both ask here.

For the any-address (``0.0.0.0``, ``::`` or empty) the port is free only
when both of these binds succeed, so a listener on any one address of
either family counts as busy (a listener on ``127.0.0.1`` or ``::1``
included):

* ``0.0.0.0`` (IPv4);
* ``[::]`` dual-stack (``IPV6_V6ONLY`` off). A host without IPv6
  (``EAFNOSUPPORT``, ``EADDRNOTAVAIL``, or no ``AF_INET6`` at all) skips
  this one.

For any other host, that one address is bound (a name as IPv4).

On Windows every probe socket sets ``SO_EXCLUSIVEADDRUSE``: without it a
bind can succeed next to a listener that did not ask for exclusive use.
Elsewhere no ``SO_REUSEADDR`` is set, so a bind next to any listener on
the port fails.
"""
from __future__ import annotations

import errno
import ipaddress
import os
import socket

ANY_HOSTS = frozenset({"", "0.0.0.0", "::"})
_NO_IPV6 = frozenset({errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, getattr(errno, "WSAEAFNOSUPPORT", -1)})


def _new_socket(family: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    if os.name == "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    return sock


def _binds(family: int, address: str, port: int) -> bool:
    with _new_socket(family) as sock:
        try:
            sock.bind((address, port))
        except OSError:
            return False
    return True


def _ipv6_binds(port: int) -> bool:
    """The dual-stack ``[::]`` bind; True on a host without IPv6."""
    if not socket.has_ipv6:
        return True
    try:
        sock = _new_socket(socket.AF_INET6)
    except OSError as exc:
        if exc.errno in _NO_IPV6:
            return True
        raise
    with sock:
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.bind(("::", port))
        except OSError as exc:
            return exc.errno in _NO_IPV6
    return True


def port_is_free(host: str, port: int) -> bool:
    """True when a TCP listener could bind ``host:port`` now (see the module doc)."""
    host = host.strip().strip("[]")
    if host in ANY_HOSTS:
        return _binds(socket.AF_INET, "0.0.0.0", port) and _ipv6_binds(port)
    try:
        family = socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
    except ValueError:  # a name, not an address literal
        family = socket.AF_INET
    return _binds(family, host, port)
