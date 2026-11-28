# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The port setup is served on (FR-LN-3).

The earlier ``[server] port`` (Reset setup on a machine that ran
before), else 8080. When that port is taken, the first free port of
8080-8089, for setup only; the banner and the tray show it. Our own
loopback ports (Caddy's 8089, Waitress's 8088 and 8090 or the
configured ones, ``launcher.caddy_wiring.loopback_ports``) are never
chosen: setup mode binds them a moment later. No free port is a clear
error, never a silent fallback.

"Free" is the exclusive probe ``shared.port_probe.port_is_free`` on the
any-address of both families (issue #350), the same one setup mode
uses. :func:`earlier_port` is the one reader of ``[server] port``
(``caddy_wiring.configured_public_port`` uses it too).

The firewall rule is tied to ``caddy.exe``, not to a port, so any of
these works (FR-IN-2).
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from shared import settings_file
from shared.port_probe import port_is_free as _probe

DEFAULT_SETUP_PORT = 8080
SETUP_PORT_RANGE = range(8080, 8090)
BIND_HOST = "0.0.0.0"


class SetupPortError(RuntimeError):
    """No port of the setup range is free."""


def port_is_free(port: int) -> bool:
    """True when Caddy could listen on ``port`` on every address now (issue #350)."""
    return _probe(BIND_HOST, port)


def earlier_port(ini_path=None) -> Optional[int]:
    """The earlier ``[server] port`` when it is a usable port number, else ``None``."""
    try:
        raw = settings_file.read_settings(ini_path).get(
            settings_file.SECTION_SERVER, settings_file.KEY_SERVER_PORT, fallback="",
        ).strip()
    except OSError:  # SettingsFileError is an OSError
        return None
    if not (raw.isascii() and raw.isdigit()):
        return None
    port = int(raw)
    return port if 1 <= port <= 65535 else None


def choose_setup_port(
    ini_path=None,
    *,
    reserved: Optional[Iterable[int]] = None,
    held_by_us: Optional[int] = None,
    is_free: Callable[[int], bool] = port_is_free,
) -> int:
    """Return the setup port (see the module doc).

    ``reserved`` are our own loopback ports (default
    ``caddy_wiring.loopback_ports``). ``held_by_us`` is the port our
    running Caddy already listens on (re-entering ``SETUP``); it counts
    as free. Raises :class:`SetupPortError` when nothing is free.
    """
    if reserved is None:
        from launcher.caddy_wiring import loopback_ports
        reserved = loopback_ports(ini_path)
    reserved = frozenset(reserved)

    def usable(port: int) -> bool:
        return port not in reserved and (port == held_by_us or is_free(port))

    preferred = earlier_port(ini_path)
    if preferred is None or preferred in reserved:
        preferred = DEFAULT_SETUP_PORT
    if usable(preferred):
        return preferred
    for port in SETUP_PORT_RANGE:
        if usable(port):
            return port
    raise SetupPortError(
        f"No free port for setup: {preferred} and {SETUP_PORT_RANGE.start}-"
        f"{SETUP_PORT_RANGE.stop - 1} are all in use"
    )
