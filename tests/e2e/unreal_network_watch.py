# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Firewall-prompt check for the real Unreal E2E.

An unattended worker must never trigger a Windows Firewall prompt. One
appears when a program path the firewall does not know listens on a
non-loopback address, and under isolation UE installs zenserver (and
the trace server) inside each farm profile, so every cache dir is a new
program path.

``FarmNetworkWatch.sample()`` runs on every poll while the jobs run. It
walks the worker's process tree (the worker's Job Object holds every UE
child) and flags:

- any UDP socket bound off loopback (UDP needs no ``listen()``);
- any TCP socket bound off loopback, with no peer, that accepts a local
  connect, except UE 5.8.4's TraceLog control listener (#233): TCP,
  owned by UnrealEditor-Cmd, on port 1985 or, when 1985 is taken, in
  0x8000-0x9FFF. The installer adds a firewall ALLOW rule for that
  binary; at most one such listener per process is allowed;
- any ``unrealtraceserver.exe`` that was not already running at start.

Why a connect probe and not the socket state: on theta's Windows build
(26300) the TCP table reports a user-mode listener's state as 0 or a
wrong value (psutil says NONE, ``netstat`` omits it, ``Get-NetTCPConnection``
shows value 0 or e.g. FinWait1 for RPC's ``0.0.0.0:135``), the same as a
socket that is only bound for an outbound connect that has not started.
A listener accepts a local connect; a socket that is only bound refuses
it. Such bound-only sockets are recorded in ``not_listening``, not failed.
"""

import ipaddress
import logging
import socket

import psutil

logger = logging.getLogger(__name__)

EDITOR = "unrealeditor-cmd.exe"
TRACE_SERVER = "unrealtraceserver.exe"
FARM_IMAGES = frozenset((EDITOR, "zenserver.exe", "shadercompileworker.exe", TRACE_SERVER))
TRACE_CONTROL_PORT = 1985
TRACE_CONTROL_FALLBACK = range(0x8000, 0xA000)
_PROBE_TIMEOUT = 1.0
# A wildcard-bound listener is reachable on loopback.
_LOCAL_TARGET = {"0.0.0.0": "127.0.0.1", "::": "::1"}


def _accepts_connections(ip, port):
    """True when a local TCP connect to ``ip:port`` is accepted (it listens)."""
    target = _LOCAL_TARGET.get(ip, ip)
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.settimeout(_PROBE_TIMEOUT)
        return probe.connect_ex((target, port)) == 0


def _is_trace_control(name, kind, port):
    return name == EDITOR and kind == "TCP" and (
        port == TRACE_CONTROL_PORT or port in TRACE_CONTROL_FALLBACK
    )


def _unconnected_off_loopback(proc):
    """``(kind, ip, port)`` of ``proc``'s sockets bound off loopback with no peer."""
    found = []
    for conn in proc.net_connections(kind="inet"):
        if not conn.laddr or conn.raddr:
            continue
        ip = conn.laddr.ip.split("%")[0]
        if not ipaddress.ip_address(ip).is_loopback:
            kind = "TCP" if conn.type == socket.SOCK_STREAM else "UDP"
            found.append((kind, ip, conn.laddr.port))
    return found


def _trace_server_pids():
    return {
        proc.info["pid"] for proc in psutil.process_iter(["name", "pid"])
        if (proc.info.get("name") or "").lower() == TRACE_SERVER
    }


class FarmNetworkWatch:
    """Samples the farm's process tree for anything that would prompt the firewall."""

    def __init__(self, worker_pid):
        self.worker_pid = worker_pid
        self.seen = set()
        self.problems = set()
        self.allowed = set()
        self.not_listening = set()
        self._old_trace = _trace_server_pids()

    def _farm_processes(self):
        try:
            tree = psutil.Process(self.worker_pid).children(recursive=True)
        except psutil.Error:
            return
        for proc in tree:
            try:
                name = proc.name().lower()
                if name in FARM_IMAGES:
                    yield name, proc.pid, _unconnected_off_loopback(proc)
            except psutil.Error:  # exited between the walk and the read
                continue

    def _classify(self, name, pid, sockets):
        allowed_one = False
        for kind, ip, port in sockets:
            where = f"{name} (pid {pid}) {kind} {ip}:{port}"
            if not allowed_one and _is_trace_control(name, kind, port):
                allowed_one = True
                self.allowed.add(f"{name} {kind} {ip}:{port}")
            elif kind == "UDP" or _accepts_connections(ip, port):
                self.problems.add(f"{name} listens on {kind} {ip}:{port}")
            elif where not in self.not_listening:
                self.not_listening.add(where)
                logger.info("Bound but not listening (refuses connections): %s", where)

    def sample(self):
        """Record the farm images seen and every off-loopback listener right now."""
        for name, pid, sockets in self._farm_processes():
            self.seen.add(name)
            self._classify(name, pid, sockets)
        for pid in _trace_server_pids() - self._old_trace:
            self.problems.add(f"{TRACE_SERVER} is running (pid {pid})")
