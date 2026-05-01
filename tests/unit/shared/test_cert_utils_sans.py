# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""SAN / NIC / expiry-warning tests for ``shared/cert_utils.py``.

Covers ``enumerate_sans``, ``_collect_nic_addresses``, and
``check_cert_expiry_warning``. Cert-generation tests live in
``test_cert_utils_generation.py``; atomic-write tests in
``test_cert_utils_atomic_writes.py``.
"""
import logging
import socket
from unittest.mock import MagicMock

from cryptography import x509

from shared.cert_utils import (
    _collect_nic_addresses,
    check_cert_expiry_warning,
    enumerate_sans,
)

from ._cert_utils_helpers import _quick_cert, _quick_key


class TestCheckCertExpiryWarning:

    def test_warns_within_90_days(self, caplog):
        key = _quick_key()
        cert = _quick_cert(key, days_valid=30)
        caplog.set_level(logging.WARNING)
        check_cert_expiry_warning(cert)
        assert any('expires in' in r.message for r in caplog.records)

    def test_no_warning_far_from_expiry(self, caplog):
        key = _quick_key()
        cert = _quick_cert(key, days_valid=3650)
        caplog.set_level(logging.WARNING)
        check_cert_expiry_warning(cert)
        assert not any('expires in' in r.message for r in caplog.records)


class TestEnumerateSans:

    def test_includes_localhost_and_loopback(self, mocker):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value=set(),
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value='myhost',
        )
        sans = enumerate_sans()
        ips = [str(s.value) for s in sans if isinstance(s, x509.IPAddress)]
        dns = [s.value for s in sans if isinstance(s, x509.DNSName)]
        assert '127.0.0.1' in ips
        assert 'localhost' in dns

    def test_includes_hostname_and_hostname_local(self, mocker):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value=set(),
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value='box',
        )
        sans = enumerate_sans()
        dns = [s.value for s in sans if isinstance(s, x509.DNSName)]
        assert 'box' in dns
        assert 'box.local' in dns

    def test_warns_no_routable_ips(self, mocker, caplog):
        mocker.patch(
            'shared.cert_utils._collect_nic_addresses',
            return_value=set(),
        )
        mocker.patch(
            'shared.cert_utils.socket.gethostname',
            return_value='h',
        )
        caplog.set_level(logging.WARNING)
        enumerate_sans()
        assert any(
            'No routable' in r.message for r in caplog.records
        )


class TestCollectNicAddresses:

    def test_returns_ipv4(self, mocker):
        addr = MagicMock(family=socket.AF_INET, address='10.0.0.1')
        mocker.patch(
            'shared.cert_utils.psutil.net_if_addrs',
            return_value={'eth0': [addr]},
        )
        assert '10.0.0.1' in _collect_nic_addresses()

    def test_skips_link_local_ipv6(self, mocker):
        addr = MagicMock(
            family=socket.AF_INET6, address='fe80::1%eth0',
        )
        mocker.patch(
            'shared.cert_utils.psutil.net_if_addrs',
            return_value={'eth0': [addr]},
        )
        result = _collect_nic_addresses()
        assert 'fe80::1' not in result

    def test_empty_interfaces(self, mocker):
        mocker.patch(
            'shared.cert_utils.psutil.net_if_addrs',
            return_value={},
        )
        assert _collect_nic_addresses() == set()
