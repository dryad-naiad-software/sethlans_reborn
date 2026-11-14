# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Farm Zen port and loopback checks (FR-UE-1 6a, FR-UE-26; AC-52, Sec R3-W2, R3-W3)."""
from collections import namedtuple

import psutil
import pytest

from sethlans_worker_agent.unreal_executor import zen_port

Addr = namedtuple('Addr', 'ip port')
Conn = namedtuple('Conn', 'fd family type laddr raddr status pid')
FARM_PID, OTHER_PID = 100, 200
CACHE = r'D:\uecache'


def conn(ip, port=8668, pid=FARM_PID, status=psutil.CONN_LISTEN):
    return Conn(-1, 2, 1, Addr(ip, port), (), status, pid)


@pytest.fixture()
def farm(monkeypatch):
    farm_pids = {FARM_PID}
    monkeypatch.setattr(zen_port.zen_private, 'is_farm_zenserver', lambda proc, cache, worker_sid=None:
                        proc in farm_pids)
    return farm_pids


def check(conns, **kwargs):
    return zen_port.check_port(8668, CACHE, net_connections=lambda kind: conns, process_factory=lambda pid: pid,
                               **kwargs)


def test_free_port_is_ok(farm):
    assert check([]).ok
    assert check([conn('0.0.0.0', port=9000, pid=OTHER_PID)]).ok
    assert check([conn('127.0.0.1', pid=OTHER_PID, status=psutil.CONN_ESTABLISHED)]).ok


def test_farm_zenserver_listener_is_ok(farm):
    result = check([conn('127.0.0.1'), conn('::1')])
    assert result.ok and len(result.farm) == 2


@pytest.mark.parametrize('ip', ['0.0.0.0', '127.0.0.1', '192.168.1.20', '::', '::1'])
def test_foreign_listener_on_any_address_blocks(farm, ip):
    result = check([conn(ip, pid=OTHER_PID)])
    assert not result.ok and result.foreign[0].address == ip
    assert 'not the farm zenserver' in result.message


def test_listener_without_pid_is_foreign(farm):
    assert not check([conn('0.0.0.0', pid=None)]).ok


def test_unreadable_owner_is_foreign(farm):
    def factory(pid):
        raise psutil.AccessDenied(pid)
    result = zen_port.check_port(8668, CACHE, net_connections=lambda kind: [conn('127.0.0.1')],
                                 process_factory=factory)
    assert not result.ok


def test_listing_failure_fails_closed(farm):
    def failing(kind):
        raise psutil.AccessDenied()
    result = zen_port.check_port(8668, CACHE, net_connections=failing)
    assert not result.ok and 'Could not check' in result.message


def test_listeners_use_both_families():
    seen = {}

    def net(kind):
        seen['kind'] = kind
        return []
    zen_port.listeners(8668, net)
    assert seen['kind'] == 'tcp'


def loop(conns):
    return zen_port.check_loopback(8668, CACHE, net_connections=lambda kind: conns, process_factory=lambda pid: pid)


def test_loopback_not_seen_until_farm_listens(farm):
    assert loop([]) == zen_port.LoopbackCheck(seen=False, ok=True)
    assert loop([conn('0.0.0.0', pid=OTHER_PID)]).seen is False


def test_loopback_only_farm_listener_passes(farm):
    result = loop([conn('127.0.0.1'), conn('::1')])
    assert result.seen and result.ok


@pytest.mark.parametrize('ip', ['0.0.0.0', '192.168.1.20', '::'])
def test_non_loopback_farm_listener_fails(farm, ip):
    result = loop([conn('127.0.0.1'), conn(ip)])
    assert result.seen and not result.ok and ip in result.addresses


@pytest.mark.parametrize('ip, expected', [('127.0.0.1', True), ('127.0.0.2', True), ('::1', True),
                                          ('::ffff:127.0.0.1', True), ('0.0.0.0', False), ('fe80::1%3', False),
                                          ('not-an-ip', False)])
def test_is_loopback(ip, expected):
    assert zen_port.is_loopback(ip) is expected
