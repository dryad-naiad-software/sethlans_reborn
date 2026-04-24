# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for Caddyfile rendering with paths that contain
spaces.

macOS per-user data dirs live under ``~/Library/Application
Support/Sethlans/...``. Unquoted cert/key arguments to the ``tls``
directive got tokenised on the space, and Caddy failed with
``wrong argument count or unexpected line ending`` — crashing the
worker's Caddy supervisor after one successful render in the
self-hosted Apple Silicon E2E job (run 24896942984).

These tests live in a separate module so the main
``test_caddy_template`` file stays under the 300-line budget.
"""

from __future__ import annotations

import pytest

from sethlans_worker_agent.caddy_template import (
    _validate_plain_string,
    render_worker_caddyfile,
)


class TestPathWithSpaces:

    def test_tls_line_quotes_paths(self, tmp_path):
        data_dir = tmp_path / 'Application Support' / 'Sethlans'
        (data_dir / 'tls').mkdir(parents=True)
        cert = data_dir / 'tls' / 'worker.crt'
        key = data_dir / 'tls' / 'worker.key'
        cert.write_text('C')
        key.write_text('K')
        out = render_worker_caddyfile(
            public_tls_port=8443,
            loopback_plaintext_port=18443,
            waitress_upstream_port=28443,
            cert_path=cert,
            key_path=key,
            worker_data_dir=data_dir,
        )
        cert_str = str(cert.resolve())
        key_str = str(key.resolve())
        assert ' ' in cert_str, "Fixture must produce a space in path"
        assert f'tls "{cert_str}" "{key_str}"' in out

    def test_double_quote_in_path_rejected(self):
        with pytest.raises(ValueError, match='must not contain'):
            _validate_plain_string('cert_path', '/bad"path/cert.pem')
