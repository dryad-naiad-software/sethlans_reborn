# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
This machine's names and addresses, and the port-free check (Django-free).
"""
import ipaddress
import logging
import os
import socket
from typing import List

import psutil

logger = logging.getLogger(__name__)

ANY_ADDRESS = "0.0.0.0"
LOOPBACK = "127.0.0.1"


def hostname() -> str:
    return socket.gethostname()


def ipv4_addresses() -> List[str]:
    """This machine's IPv4 addresses (loopback excluded), sorted."""
    found = set()
    try:
        interfaces = psutil.net_if_addrs()
    except OSError as exc:  # pragma: no cover - psutil failure
        logger.warning("Could not list the network interfaces: %s", exc)
        return []
    for addrs in interfaces.values():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                found.add(addr.address)
    return sorted(found, key=ipaddress.IPv4Address)


def bind_addresses() -> List[str]:
    """The addresses a manager may bind: any, loopback, or one of this machine's."""
    return [ANY_ADDRESS, LOOPBACK] + ipv4_addresses()


def allowed_hosts(extra_host: str = "") -> List[str]:
    """``ALLOWED_HOSTS`` for setup mode (FR-SM-8)."""
    name = hostname()
    hosts = ["localhost", LOOPBACK, name, f"{name}.local"]
    fqdn = socket.getfqdn()
    if fqdn and fqdn not in hosts:
        hosts.append(fqdn)
    hosts += ipv4_addresses()
    if extra_host and extra_host != ANY_ADDRESS and extra_host not in hosts:
        hosts.append(extra_host)
    return hosts


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` and loopback addresses (a database on this machine)."""
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


def port_is_free(host: str, port: int) -> bool:
    """True when a TCP listener could bind ``host:port`` now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True
