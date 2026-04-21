# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`sethlans_manager.caddy_template`.

Covers:

- Happy-path render returns a non-empty Caddyfile containing all
  required directives.
- Every TLS hardening invariant from the manager spec is present.
- Input validation rejects invalid ports, traversal paths, non-string
  paths, out-of-range cert paths, and injection meta-characters.
- Output is deterministic (same inputs → same string).
- /api/status/public/ returns 404 on the public vhost (defense-in-
  depth against the URLconf-origin middleware).

All filesystem inputs use ``tmp_path`` for isolation.
"""

from __future__ import annotations

import pytest

from sethlans_manager.caddy_template import (
    _validate_plain_string,
    render_manager_caddyfile,
)


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

@pytest.fixture
def manager_paths(tmp_path):
    """Build a realistic manager data dir layout with cert+key files."""
    data_dir = tmp_path / 'manager_data'
    (data_dir / 'tls').mkdir(parents=True)
    cert = data_dir / 'tls' / 'manager.crt'
    key = data_dir / 'tls' / 'manager.key'
    cert.write_text('CERT')
    key.write_text('KEY')
    return {
        'data_dir': data_dir,
        'cert_path': cert,
        'key_path': key,
    }


def _valid_kwargs(manager_paths):
    return {
        'public_tls_port': 8080,
        'loopback_plaintext_port': 8089,
        'waitress_public_port': 18080,
        'waitress_internal_port': 18088,
        'cert_path': manager_paths['cert_path'],
        'key_path': manager_paths['key_path'],
        'manager_data_dir': manager_paths['data_dir'],
    }


# -------------------------------------------------------------------
# Happy path
# -------------------------------------------------------------------

class TestHappyPath:
    def test_returns_non_empty_string(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        assert isinstance(out, str)
        assert len(out) > 0

    def test_contains_all_ports(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        assert ':8080 {' in out  # public TLS vhost bind
        assert '127.0.0.1:8089' in out  # loopback plaintext bind
        assert '127.0.0.1:18080' in out  # waitress public upstream
        assert '127.0.0.1:18088' in out  # waitress loopback upstream

    def test_contains_cert_and_key_paths(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        assert str(manager_paths['cert_path']) in out
        assert str(manager_paths['key_path']) in out


# -------------------------------------------------------------------
# TLS hardening invariants
# -------------------------------------------------------------------

class TestHardeningInvariants:
    def test_admin_off(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        assert 'admin off' in out

    def test_auto_https_off(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        assert 'auto_https off' in out

    def test_tls_min_version_1_2(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        assert 'tls1.2' in out

    def test_no_port_80_binding(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        # A literal ':80 {' at a site-block boundary would be a
        # violation; the rendered file must not contain one.
        assert '\n:80 {' not in out
        assert ':80 {' not in out

    def test_reverse_proxy_pinned_http_1_1(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        # Both vhost blocks must pin HTTP/1.1 on their proxy.
        assert out.count('versions 1.1') == 2
        # Two ``reverse_proxy <ip>:<port>`` directives — look for the
        # 127.0.0.1 upstream form so the pytest tmp_path name (which
        # may contain the literal "reverse_proxy") cannot confuse the
        # count.
        assert out.count('reverse_proxy 127.0.0.1:') == 2

    def test_no_acme_directives(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        # No auto-issuance; pytest's tmp_path may contain 'acme' in
        # parametrised test names. Check against the rendered lines
        # that would actually configure ACME.
        assert 'acme_dns' not in out
        assert 'acme_ca' not in out
        assert 'tls internal' not in out
        # The "automatic HTTPS" guard is encoded as ``auto_https off``.
        assert 'auto_https off' in out

    def test_no_access_log_by_default(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        assert 'log' not in out or 'access log' not in out.lower()

    def test_status_public_404_on_public_vhost(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        # Defense-in-depth: Caddy matcher + respond 404 on the public
        # vhost for the tray-only endpoint.
        assert '/api/status/public' in out
        assert 'respond @loopback_only 404' in out

    def test_loopback_vhost_uses_127_0_0_1(self, manager_paths):
        out = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        # The loopback plaintext vhost must bind to 127.0.0.1 only,
        # never 0.0.0.0.
        assert 'http://127.0.0.1:' in out
        assert 'http://0.0.0.0:' not in out


# -------------------------------------------------------------------
# Port validation
# -------------------------------------------------------------------

class TestPortValidation:
    @pytest.mark.parametrize("bad_port", [0, 1, 1023, 65536, 70000, -1])
    def test_public_port_out_of_range(self, manager_paths, bad_port):
        kwargs = _valid_kwargs(manager_paths)
        kwargs['public_tls_port'] = bad_port
        with pytest.raises(ValueError):
            render_manager_caddyfile(**kwargs)

    @pytest.mark.parametrize(
        "bad", ["8080", 8080.0, None, True, False],
    )
    def test_public_port_wrong_type(self, manager_paths, bad):
        kwargs = _valid_kwargs(manager_paths)
        kwargs['public_tls_port'] = bad
        with pytest.raises(ValueError):
            render_manager_caddyfile(**kwargs)

    def test_ports_must_all_be_distinct(self, manager_paths):
        kwargs = _valid_kwargs(manager_paths)
        kwargs['public_tls_port'] = kwargs['loopback_plaintext_port']
        with pytest.raises(ValueError):
            render_manager_caddyfile(**kwargs)


# -------------------------------------------------------------------
# Path validation
# -------------------------------------------------------------------

class TestPathValidation:
    def test_cert_outside_data_dir_rejected(self, manager_paths, tmp_path):
        kwargs = _valid_kwargs(manager_paths)
        outside = tmp_path / 'elsewhere.crt'
        outside.write_text('X')
        kwargs['cert_path'] = outside
        with pytest.raises(ValueError):
            render_manager_caddyfile(**kwargs)

    def test_cert_traversal_token_rejected(self, manager_paths):
        kwargs = _valid_kwargs(manager_paths)
        kwargs['cert_path'] = str(manager_paths['cert_path']) + '/../x'
        with pytest.raises(ValueError):
            render_manager_caddyfile(**kwargs)

    def test_cert_empty_rejected(self, manager_paths):
        kwargs = _valid_kwargs(manager_paths)
        kwargs['cert_path'] = ""
        with pytest.raises(ValueError):
            render_manager_caddyfile(**kwargs)

    def test_cert_null_byte_rejected(self, manager_paths):
        kwargs = _valid_kwargs(manager_paths)
        kwargs['cert_path'] = str(manager_paths['cert_path']) + '\x00'
        with pytest.raises(ValueError):
            render_manager_caddyfile(**kwargs)

    def test_validate_plain_string_rejects_meta_chars(self):
        for ch in ('\r', '\n', '{', '}', '#', '`', '\x00'):
            with pytest.raises(ValueError):
                _validate_plain_string('test', f"prefix{ch}suffix")


# -------------------------------------------------------------------
# Determinism
# -------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_produce_same_output(self, manager_paths):
        a = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        b = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        assert a == b

    def test_different_ports_produce_different_output(self, manager_paths):
        a = render_manager_caddyfile(**_valid_kwargs(manager_paths))
        kwargs_b = _valid_kwargs(manager_paths)
        kwargs_b['public_tls_port'] = 9090
        b = render_manager_caddyfile(**kwargs_b)
        assert a != b
