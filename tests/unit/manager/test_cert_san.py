# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for SAN enumeration helpers in cert_utils.

Covers _collect_nic_addresses, _build_san_entries, and enumerate_sans.
"""
import logging
import socket
from ipaddress import IPv4Address
from unittest.mock import MagicMock

from cryptography import x509

from sethlans_manager.cert_utils import (
    _build_san_entries,
    _collect_nic_addresses,
    enumerate_sans,
)


# --- _collect_nic_addresses ---

class TestCollectNicAddresses:

    def test_returns_ipv4_addresses(self, mocker):
        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = '192.168.1.100'
        mocker.patch(
            'sethlans_manager.cert_utils.psutil.net_if_addrs',
            return_value={'eth0': [mock_addr]}
        )
        result = _collect_nic_addresses()
        assert '192.168.1.100' in result

    def test_returns_ipv6_addresses_excluding_link_local(self, mocker):
        link_local = MagicMock()
        link_local.family = socket.AF_INET6
        link_local.address = 'fe80::1%eth0'

        global_v6 = MagicMock()
        global_v6.family = socket.AF_INET6
        global_v6.address = '2001:db8::1'

        mocker.patch(
            'sethlans_manager.cert_utils.psutil.net_if_addrs',
            return_value={'eth0': [link_local, global_v6]}
        )
        result = _collect_nic_addresses()
        assert '2001:db8::1' in result
        assert 'fe80::1' not in result

    def test_handles_empty_interfaces(self, mocker):
        mocker.patch(
            'sethlans_manager.cert_utils.psutil.net_if_addrs',
            return_value={}
        )
        result = _collect_nic_addresses()
        assert result == set()

    def test_skips_non_ip_families(self, mocker):
        mock_addr = MagicMock()
        mock_addr.family = -1
        mocker.patch(
            'sethlans_manager.cert_utils.psutil.net_if_addrs',
            return_value={'lo': [mock_addr]}
        )
        result = _collect_nic_addresses()
        assert result == set()


# --- _build_san_entries ---

class TestBuildSanEntries:

    def test_builds_ip_and_dns_entries(self):
        sans = _build_san_entries({'127.0.0.1'}, {'localhost'})
        ip_sans = [s for s in sans if isinstance(s, x509.IPAddress)]
        dns_sans = [s for s in sans if isinstance(s, x509.DNSName)]
        assert len(ip_sans) == 1
        assert len(dns_sans) == 1
        assert ip_sans[0].value == IPv4Address('127.0.0.1')
        assert dns_sans[0].value == 'localhost'

    def test_skips_invalid_ips(self, caplog):
        caplog.set_level(logging.DEBUG)
        sans = _build_san_entries({'not-an-ip'}, set())
        assert len(sans) == 0

    def test_entries_are_sorted(self):
        sans = _build_san_entries(
            {'192.168.1.2', '10.0.0.1'},
            {'z-host', 'a-host'}
        )
        dns_values = [
            s.value for s in sans if isinstance(s, x509.DNSName)
        ]
        assert dns_values == ['a-host', 'z-host']


# --- enumerate_sans ---

class TestEnumerateSans:

    def test_always_includes_loopback_and_localhost(self, mocker):
        mocker.patch(
            'sethlans_manager.cert_utils._collect_nic_addresses',
            return_value=set()
        )
        mocker.patch(
            'sethlans_manager.cert_utils.socket.gethostname',
            return_value='myhost'
        )
        sans = enumerate_sans()
        ip_values = [
            str(s.value) for s in sans
            if isinstance(s, x509.IPAddress)
        ]
        dns_values = [
            s.value for s in sans if isinstance(s, x509.DNSName)
        ]
        assert '127.0.0.1' in ip_values
        assert 'localhost' in dns_values

    def test_includes_hostname_and_hostname_local(self, mocker):
        mocker.patch(
            'sethlans_manager.cert_utils._collect_nic_addresses',
            return_value=set()
        )
        mocker.patch(
            'sethlans_manager.cert_utils.socket.gethostname',
            return_value='render-box'
        )
        sans = enumerate_sans()
        dns_values = [
            s.value for s in sans if isinstance(s, x509.DNSName)
        ]
        assert 'render-box' in dns_values
        assert 'render-box.local' in dns_values

    def test_includes_nic_ip_addresses(self, mocker):
        mocker.patch(
            'sethlans_manager.cert_utils._collect_nic_addresses',
            return_value={'10.0.0.5'}
        )
        mocker.patch(
            'sethlans_manager.cert_utils.socket.gethostname',
            return_value='host'
        )
        sans = enumerate_sans()
        ip_values = [
            str(s.value) for s in sans
            if isinstance(s, x509.IPAddress)
        ]
        assert '10.0.0.5' in ip_values

    def test_warns_when_no_routable_ips(self, mocker, caplog):
        mocker.patch(
            'sethlans_manager.cert_utils._collect_nic_addresses',
            return_value=set()
        )
        mocker.patch(
            'sethlans_manager.cert_utils.socket.gethostname',
            return_value='lonely'
        )
        caplog.set_level(logging.WARNING)
        enumerate_sans()
        assert any(
            'No routable network interfaces' in r.message
            for r in caplog.records
        )

    def test_no_warning_when_routable_ip_present(self, mocker, caplog):
        mocker.patch(
            'sethlans_manager.cert_utils._collect_nic_addresses',
            return_value={'192.168.1.50'}
        )
        mocker.patch(
            'sethlans_manager.cert_utils.socket.gethostname',
            return_value='host'
        )
        caplog.set_level(logging.WARNING)
        enumerate_sans()
        assert not any(
            'No routable network interfaces' in r.message
            for r in caplog.records
        )
