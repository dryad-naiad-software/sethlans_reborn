# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Farm Zen port checks (Unreal foundation FR-UE-1 check 6a, FR-UE-26;
Sec R3-W1, R3-W2, R3-W3).

* ``check_port(port, cache_dir)``: the port is usable when no TCP socket
  is in ``LISTEN`` state on it, on **any** local address (IPv4 or IPv6:
  ``0.0.0.0``, ``127.0.0.1``, a LAN address, ``::``, ``::1``), or when
  every listener is this worker's own farm zenserver
  (``zen_private.is_farm_zenserver``). A listener without an owner PID, an
  owner that cannot be read, or a listing that fails counts as foreign
  (fail closed). The caller repeats it at claim time and immediately
  before every UE launch.
* ``check_loopback(port, cache_dir)``: during a phase, once a farm
  zenserver listens on the port, every address it listens on must be
  loopback; otherwise the phase is terminated, the job released with
  ``local_conflict`` and Unreal disabled until restart (the caller does
  that; ``LOOPBACK_DISABLE_REASON`` is the reason text).

Listeners come from ``psutil.net_connections(kind='tcp')`` (both address
families); see ``_is_listener`` for the ``NONE`` status quirk.
"""
import ipaddress
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import psutil

from sethlans_worker_agent.unreal_executor import zen_private

LOOPBACK_DISABLE_REASON = "farm zenserver listened on a non-loopback address"


@dataclass(frozen=True)
class Listener:
    pid: Optional[int]
    address: str


@dataclass(frozen=True)
class PortCheck:
    ok: bool
    foreign: Tuple[Listener, ...] = ()
    farm: Tuple[Listener, ...] = ()
    error: str = ''

    @property
    def message(self) -> str:
        if self.ok:
            return ''
        if self.error:
            return f"Could not check the farm Zen port: {self.error}"
        first = self.foreign[0]
        owner = f"PID {first.pid}" if first.pid else "an unknown process"
        return f"The farm Zen port is held by {owner} on {first.address or '*'}, not the farm zenserver."


@dataclass(frozen=True)
class LoopbackCheck:
    seen: bool               # a farm zenserver listens on the port
    ok: bool                 # every farm listener is loopback (True while not seen)
    addresses: Tuple[str, ...] = ()


def _is_listener(conn) -> bool:
    """``LISTEN``, or a socket with no remote end whose state psutil cannot name.

    On theta (Windows 11 26300, psutil 7.0.0) a listening loopback socket is
    reported with status ``NONE``; counting it keeps the check fail-closed
    (a bound socket on the port blocks the farm zenserver as well).
    """
    if conn.status == psutil.CONN_LISTEN:
        return True
    return conn.status == psutil.CONN_NONE and not conn.raddr


def listeners(port: int, net_connections: Callable = psutil.net_connections) -> List[Listener]:
    """Every TCP ``LISTEN`` socket on ``port``, any local address. Raises ``psutil.Error``/``OSError``."""
    found = []
    for conn in net_connections(kind='tcp'):
        if not conn.laddr or not _is_listener(conn):
            continue
        if int(conn.laddr.port) == int(port):
            found.append(Listener(pid=conn.pid or None, address=str(conn.laddr.ip)))
    return found


def _is_farm(listener: Listener, cache_dir, worker_sid, process_factory) -> bool:
    if not listener.pid:
        return False
    try:
        proc = process_factory(listener.pid)
    except (psutil.Error, OSError, ValueError):
        return False
    return zen_private.is_farm_zenserver(proc, cache_dir, worker_sid=worker_sid)


def _classify(port, cache_dir, worker_sid, net_connections, process_factory):
    found = listeners(port, net_connections)
    farm = tuple(item for item in found if _is_farm(item, cache_dir, worker_sid, process_factory))
    foreign = tuple(item for item in found if item not in farm)
    return farm, foreign


def check_port(port: int, cache_dir, worker_sid: Optional[str] = None,
               net_connections: Callable = psutil.net_connections,
               process_factory: Callable = psutil.Process) -> PortCheck:
    """Is ``port`` free or held only by the farm zenserver?"""
    try:
        farm, foreign = _classify(port, cache_dir, worker_sid, net_connections, process_factory)
    except (psutil.Error, OSError) as exc:
        return PortCheck(ok=False, error=f"{type(exc).__name__}: {exc}")
    return PortCheck(ok=not foreign, foreign=foreign, farm=farm)


def is_loopback(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split('%', 1)[0])
    except ValueError:
        return False
    mapped = getattr(parsed, 'ipv4_mapped', None)
    return (mapped or parsed).is_loopback


def check_loopback(port: int, cache_dir, worker_sid: Optional[str] = None,
                   net_connections: Callable = psutil.net_connections,
                   process_factory: Callable = psutil.Process) -> LoopbackCheck:
    """Loopback-only rule for the farm zenserver's listening sockets (Sec R3-W3).

    A listing failure reads as "not seen yet": the next tick retries, and
    the port re-check before the next launch fails closed on its own.
    """
    try:
        farm, _foreign = _classify(port, cache_dir, worker_sid, net_connections, process_factory)
    except (psutil.Error, OSError):
        return LoopbackCheck(seen=False, ok=True)
    if not farm:
        return LoopbackCheck(seen=False, ok=True)
    addresses = tuple(sorted({item.address for item in farm}))
    return LoopbackCheck(seen=True, ok=all(is_loopback(addr) for addr in addresses), addresses=addresses)


class LoopbackWatch:
    """``RunSpec.tick_check`` for one phase: the loopback rule until the farm listener is seen.

    Returns ``None`` while all is well and the reason text once the farm
    zenserver listens on a non-loopback address; after a passing sighting it
    stops querying (FR-UE-26: "checked on each monitor tick until seen").

    **Contract:** one fresh ``LoopbackWatch`` per phase (a new UE launch
    starts a new zenserver, so a sighting does not carry over). It logs
    nothing; foreign listeners are the pre-launch ``check_port``'s concern.
    """

    def __init__(self, port: int, cache_dir, worker_sid: Optional[str] = None, check: Callable = None):
        self._port, self._cache_dir, self._sid = port, cache_dir, worker_sid
        self._check = check or check_loopback
        self.seen = False
        self.addresses: Tuple[str, ...] = ()

    def __call__(self) -> Optional[str]:
        if self.seen:
            return None
        result = self._check(self._port, self._cache_dir, worker_sid=self._sid)
        if not result.seen:
            return None
        self.seen, self.addresses = True, result.addresses
        if result.ok:
            return None
        return f"{LOOPBACK_DISABLE_REASON} ({', '.join(result.addresses)})"
