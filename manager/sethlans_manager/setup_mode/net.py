# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
This machine's names and addresses, and the port-free check (Django-free).

``port_is_free`` is ``shared.port_probe.port_is_free`` (issue #350), the one
exclusive probe the launcher uses too; it is re-exported here so callers
(``validate``) and tests keep one name to use and patch.
"""
import ipaddress
import logging
import socket
import threading
from typing import List

import psutil

from shared.port_probe import port_is_free  # noqa: F401 - re-exported for validate and tests

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


def ipv6_addresses() -> List[str]:
    """This machine's IPv6 addresses, loopback and link-local excluded (a
    link-local address needs a zone, which a browser's Host cannot carry)."""
    found = set()
    try:
        interfaces = psutil.net_if_addrs()
    except OSError:  # pragma: no cover - psutil failure, logged by ipv4_addresses
        return []
    for addrs in interfaces.values():
        for addr in addrs:
            if addr.family != socket.AF_INET6:
                continue
            try:
                address = ipaddress.IPv6Address(addr.address.split("%", 1)[0])
            except ValueError:
                continue
            if not (address.is_loopback or address.is_link_local):
                found.add(address.compressed)
    return sorted(found)


def bind_addresses() -> List[str]:
    """The addresses a manager may bind: any, loopback, or one of this machine's."""
    return [ANY_ADDRESS, LOOPBACK] + ipv4_addresses()


def _add_fqdn_later(hosts: List[str]) -> None:
    """Append ``socket.getfqdn()`` to ``hosts`` from a daemon thread.

    The lookup can wait on DNS for many seconds, so it never runs on the
    importing thread. Django reads ``ALLOWED_HOSTS`` (this same list) on
    each request, so the name is accepted from the moment it is known.
    """
    def lookup():
        try:
            fqdn = socket.getfqdn()
        except OSError:
            return
        if fqdn and fqdn not in hosts:
            hosts.append(fqdn)

    threading.Thread(target=lookup, name="setup-fqdn", daemon=True).start()


def allowed_hosts(extra_host: str = "", *, look_up_fqdn: bool = True) -> List[str]:
    """``ALLOWED_HOSTS`` for setup mode (FR-SM-8).

    Names and IPv4 literals as sent; IPv6 literals in brackets, which is
    how Django compares them (``[::1]``, ``[2001:db8::5]``).
    """
    name = hostname()
    hosts = ["localhost", LOOPBACK, "[::1]", name, f"{name}.local"]
    hosts += ipv4_addresses()
    hosts += [f"[{address}]" for address in ipv6_addresses()]
    if extra_host and extra_host != ANY_ADDRESS:
        literal = f"[{extra_host}]" if ":" in extra_host and not extra_host.startswith("[") else extra_host
        if literal not in hosts:
            hosts.append(literal)
    if look_up_fqdn:
        _add_fqdn_later(hosts)
    return hosts


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` and loopback addresses (a database on this machine)."""
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False
