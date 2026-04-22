# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`sethlans_worker_agent.caddy_template`.

Covers:

- Happy-path render returns a non-empty Caddyfile containing all
  required directives.
- Every TLS hardening invariant from the manager spec is present.
- Input validation rejects invalid ports, traversal paths, non-string
  paths, out-of-range cert paths, and injection meta-characters.
- Output is deterministic (same inputs → same string).

All filesystem inputs use ``tmp_path`` for isolation.
"""

from __future__ import annotations

import pytest

from sethlans_worker_agent.caddy_template import (
    _validate_plain_string,
    render_worker_caddyfile,
)


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

@pytest.fixture
def worker_paths(tmp_path):
    """Build a realistic worker data dir layout with cert+key files.

    Returns a dict of {data_dir, cert_path, key_path} that satisfies
    the containment check in ``render_worker_caddyfile``.
    """
    data_dir = tmp_path / 'worker_data'
    (data_dir / 'tls').mkdir(parents=True)
    cert = data_dir / 'tls' / 'worker.crt'
    key = data_dir / 'tls' / 'worker.key'
    cert.write_text('CERT')
    key.write_text('KEY')
    return {
        'data_dir': data_dir,
        'cert_path': cert,
        'key_path': key,
    }


def _valid_kwargs(worker_paths):
    return {
        'public_tls_port': 8443,
        'loopback_plaintext_port': 18443,
        'waitress_upstream_port': 28443,
        'cert_path': worker_paths['cert_path'],
        'key_path': worker_paths['key_path'],
        'worker_data_dir': worker_paths['data_dir'],
    }


# -------------------------------------------------------------------
# Happy path
# -------------------------------------------------------------------

class TestHappyPath:
    def test_returns_non_empty_string(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        assert isinstance(out, str)
        assert len(out) > 0

    def test_contains_all_ports(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        assert ':8443' in out          # public TLS bind
        assert ':18443' in out         # loopback plaintext bind
        assert '127.0.0.1:28443' in out  # upstream Waitress

    def test_contains_cert_and_key_paths(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        assert str(worker_paths['cert_path'].resolve()) in out
        assert str(worker_paths['key_path'].resolve()) in out


# -------------------------------------------------------------------
# TLS hardening invariants (mirror of manager spec)
# -------------------------------------------------------------------

class TestHardeningInvariants:
    def test_admin_off(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        assert 'admin off' in out

    def test_auto_https_off(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        assert 'auto_https off' in out

    def test_tls_min_version_1_2(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        # Caddy v2 `protocols tls1.2 tls1.3` pins the floor at TLS 1.2.
        assert 'tls1.2' in out
        # SSLv3/TLS 1.0/1.1 must NOT be allowed.
        assert 'tls1.0' not in out
        assert 'tls1.1' not in out
        assert 'sslv3' not in out.lower()

    def test_no_port_80_binding(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        # No bare `:80` site block, no `http://<host>:80` bind.
        for line in out.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(':80 ')
            assert not stripped.startswith(':80\t')
            assert not stripped == ':80'
            assert not stripped.startswith(':80{')
            assert not stripped.startswith(':80 {')

    def test_reverse_proxy_pinned_http_1_1(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        assert 'reverse_proxy' in out
        assert 'versions 1.1' in out

    def test_no_acme_directives(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        # Strip cert/key path lines before the ACME check: pytest's
        # tmp_path may embed the containing test name (e.g.
        # ``test_no_acme_directives0``) in the paths, which would
        # make this assertion trivially fail on a substring match.
        cert_str = str(worker_paths['cert_path'].resolve())
        key_str = str(worker_paths['key_path'].resolve())
        scrubbed = out.replace(cert_str, '').replace(key_str, '')
        lower = scrubbed.lower()
        assert 'acme' not in lower
        assert 'letsencrypt' not in lower
        assert 'zerossl' not in lower

    def test_no_access_log_by_default(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        # Phase 1 must not add an access log directive. If the spec
        # ever adds one, Authorization/X-Setup-Token/Cookie must be
        # redacted first; until then the directive must not appear.
        assert 'log {' not in out
        assert 'log\n' not in out

    def test_setup_paths_404_on_public_vhost(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        # /setup/* and /api/setup/* must be explicitly matched and
        # 404'd on the public vhost.
        assert '/setup' in out
        assert '/api/setup' in out
        assert 'respond @setup_paths 404' in out

    def test_loopback_vhost_uses_127_0_0_1(self, worker_paths):
        out = render_worker_caddyfile(**_valid_kwargs(worker_paths))
        # Loopback vhost must bind to 127.0.0.1, not 0.0.0.0 / *.
        assert 'http://127.0.0.1:18443' in out


# -------------------------------------------------------------------
# Input validation — ports
# -------------------------------------------------------------------

class TestPortValidation:
    @pytest.mark.parametrize('bad', [0, 1, 1023, 65536, 70000, -1])
    def test_public_port_out_of_range(self, worker_paths, bad):
        kwargs = _valid_kwargs(worker_paths)
        kwargs['public_tls_port'] = bad
        with pytest.raises(ValueError, match='public_tls_port'):
            render_worker_caddyfile(**kwargs)

    @pytest.mark.parametrize('bad', ['8443', 8443.0, None, True, False])
    def test_public_port_wrong_type(self, worker_paths, bad):
        kwargs = _valid_kwargs(worker_paths)
        kwargs['public_tls_port'] = bad
        with pytest.raises(ValueError, match='public_tls_port'):
            render_worker_caddyfile(**kwargs)

    def test_loopback_port_out_of_range(self, worker_paths):
        kwargs = _valid_kwargs(worker_paths)
        kwargs['loopback_plaintext_port'] = 80
        with pytest.raises(
            ValueError, match='loopback_plaintext_port',
        ):
            render_worker_caddyfile(**kwargs)

    def test_upstream_port_out_of_range(self, worker_paths):
        kwargs = _valid_kwargs(worker_paths)
        kwargs['waitress_upstream_port'] = 99999
        with pytest.raises(
            ValueError, match='waitress_upstream_port',
        ):
            render_worker_caddyfile(**kwargs)

    def test_ports_must_be_distinct(self, worker_paths):
        kwargs = _valid_kwargs(worker_paths)
        kwargs['loopback_plaintext_port'] = kwargs['public_tls_port']
        with pytest.raises(ValueError, match='distinct'):
            render_worker_caddyfile(**kwargs)


# -------------------------------------------------------------------
# Input validation — paths
# -------------------------------------------------------------------

class TestPathValidation:
    def test_cert_outside_data_dir_rejected(
        self, worker_paths, tmp_path,
    ):
        # A cert file in a sibling directory outside the worker data
        # dir must be rejected.
        stray = tmp_path / 'stray.crt'
        stray.write_text('CERT')
        kwargs = _valid_kwargs(worker_paths)
        kwargs['cert_path'] = stray
        with pytest.raises(ValueError, match='cert_path'):
            render_worker_caddyfile(**kwargs)

    def test_key_outside_data_dir_rejected(
        self, worker_paths, tmp_path,
    ):
        stray = tmp_path / 'stray.key'
        stray.write_text('KEY')
        kwargs = _valid_kwargs(worker_paths)
        kwargs['key_path'] = stray
        with pytest.raises(ValueError, match='key_path'):
            render_worker_caddyfile(**kwargs)

    def test_cert_traversal_token_rejected(self, worker_paths):
        kwargs = _valid_kwargs(worker_paths)
        # Construct a raw string with `..` as a path component.
        kwargs['cert_path'] = (
            str(worker_paths['data_dir']) + '/tls/../../evil.crt'
        )
        with pytest.raises(ValueError, match='cert_path'):
            render_worker_caddyfile(**kwargs)

    def test_cert_empty_string_rejected(self, worker_paths):
        kwargs = _valid_kwargs(worker_paths)
        kwargs['cert_path'] = ''
        with pytest.raises(ValueError, match='cert_path'):
            render_worker_caddyfile(**kwargs)

    def test_cert_wrong_type_rejected(self, worker_paths):
        kwargs = _valid_kwargs(worker_paths)
        kwargs['cert_path'] = 12345
        with pytest.raises(ValueError, match='cert_path'):
            render_worker_caddyfile(**kwargs)

    def test_cert_path_null_byte_rejected(self, worker_paths):
        # Defense-in-depth: a null byte embedded in a raw path must
        # be rejected up front by ``_validate_path_under`` before
        # any Path(...) construction runs, so C-extension truncation
        # cannot create a confusion window.
        kwargs = _valid_kwargs(worker_paths)
        kwargs['cert_path'] = (
            str(worker_paths['data_dir']) + '/tls/worker\x00.crt'
        )
        with pytest.raises(ValueError, match='null'):
            render_worker_caddyfile(**kwargs)

    def test_validate_plain_string_rejects_null_byte(self):
        # Direct coverage of ``_validate_plain_string``: a string
        # input containing a null byte must be rejected by the
        # meta-char check (the second layer of defense behind the
        # path-traversal / containment checks).
        with pytest.raises(ValueError, match='name'):
            _validate_plain_string('name', 'hello\x00world')


# -------------------------------------------------------------------
# Determinism
# -------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_produce_same_output(self, worker_paths):
        kwargs = _valid_kwargs(worker_paths)
        out_a = render_worker_caddyfile(**kwargs)
        out_b = render_worker_caddyfile(**kwargs)
        assert out_a == out_b

    def test_different_ports_produce_different_output(
        self, worker_paths,
    ):
        kwargs_a = _valid_kwargs(worker_paths)
        kwargs_b = _valid_kwargs(worker_paths)
        kwargs_b['public_tls_port'] = 9443
        out_a = render_worker_caddyfile(**kwargs_a)
        out_b = render_worker_caddyfile(**kwargs_b)
        assert out_a != out_b
