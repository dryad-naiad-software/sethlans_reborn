# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/caddy_template.py``.

Covers issue #170 AC-WizardCaddyTemplate: the wizard Caddyfile contains
``admin off``, ``auto_https off``, ``protocols tls1.2 tls1.3``,
explicit ``tls cert key``, and ``reverse_proxy http://127.0.0.1:<loop>``.

Mirrors the structure of ``tests/unit/manager/test_caddy_template.py``
and ``tests/unit/worker/test_caddy_template.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wizard.sethlans_wizard.caddy_template import render_wizard_caddyfile


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture
def wizard_tree(tmp_path):
    """Return a wizard data dir with cert + key files pre-written."""
    data_dir = tmp_path / "wizard"
    (data_dir / "tls").mkdir(parents=True)
    cert_path = data_dir / "tls" / "cert.pem"
    key_path = data_dir / "tls" / "key.pem"
    cert_path.write_text("CERT")
    key_path.write_text("KEY")
    return {
        "data_dir": data_dir,
        "cert_path": cert_path,
        "key_path": key_path,
    }


def _render(wizard_tree, **overrides) -> str:
    kwargs = dict(
        public_tls_port=8100,
        loopback_port=8099,
        cert_path=wizard_tree["cert_path"],
        key_path=wizard_tree["key_path"],
        wizard_data_dir=wizard_tree["data_dir"],
    )
    kwargs.update(overrides)
    return render_wizard_caddyfile(**kwargs)


# ---------------------------------------------------------------------
# AC-WizardCaddyTemplate — TLS hardening invariants
# ---------------------------------------------------------------------

class TestTLSHardening:

    def test_admin_off(self, wizard_tree):
        text = _render(wizard_tree)
        assert "admin off" in text

    def test_auto_https_off(self, wizard_tree):
        text = _render(wizard_tree)
        assert "auto_https off" in text

    def test_tls_protocols_pinned(self, wizard_tree):
        text = _render(wizard_tree)
        assert "protocols tls1.2 tls1.3" in text

    def test_explicit_cert_key_directive(self, wizard_tree):
        text = _render(wizard_tree)
        cert_str = str(wizard_tree["cert_path"].resolve())
        key_str = str(wizard_tree["key_path"].resolve())
        # Paths are rendered double-quoted (paths with spaces survive).
        assert f'tls "{cert_str}" "{key_str}"' in text


# ---------------------------------------------------------------------
# AC-WizardCaddyTemplate — reverse-proxy points at loopback
# ---------------------------------------------------------------------

class TestReverseProxy:

    def test_proxies_to_loopback(self, wizard_tree):
        text = _render(wizard_tree, loopback_port=8099)
        assert "reverse_proxy http://127.0.0.1:8099" in text

    def test_http1_transport_pin(self, wizard_tree):
        text = _render(wizard_tree)
        assert "transport http" in text
        assert "versions 1.1" in text

    def test_public_listener_uses_public_port(self, wizard_tree):
        text = _render(wizard_tree, public_tls_port=8100)
        # The site block is keyed on ``:<port>``.
        assert ":8100 {" in text

    def test_no_unexpected_loopback_vhost(self, wizard_tree):
        """Wizard has only ONE vhost — the public TLS listener.

        Unlike the manager template (public + loopback split for the
        tray helper) the wizard template MUST NOT emit a second
        ``http://127.0.0.1:<port> {`` block.
        """
        text = _render(wizard_tree)
        assert text.count("http://127.0.0.1:") == 1


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

class TestValidation:

    def test_rejects_invalid_public_port(self, wizard_tree):
        with pytest.raises(ValueError, match="public_tls_port"):
            _render(wizard_tree, public_tls_port=80)

    def test_rejects_invalid_loopback_port(self, wizard_tree):
        with pytest.raises(ValueError, match="loopback_port"):
            _render(wizard_tree, loopback_port=70000)

    def test_rejects_non_int_port(self, wizard_tree):
        with pytest.raises(ValueError, match="public_tls_port"):
            _render(wizard_tree, public_tls_port="8100")

    def test_rejects_bool_port(self, wizard_tree):
        # bool is a subclass of int in Python; the validator excludes
        # it explicitly so True doesn't sneak through as port=1.
        with pytest.raises(ValueError, match="public_tls_port"):
            _render(wizard_tree, public_tls_port=True)

    def test_rejects_equal_ports(self, wizard_tree):
        with pytest.raises(ValueError, match="distinct|differ"):
            _render(wizard_tree, public_tls_port=8100, loopback_port=8100)

    def test_rejects_cert_outside_data_dir(self, wizard_tree, tmp_path):
        outside = tmp_path / "elsewhere" / "cert.pem"
        outside.parent.mkdir()
        outside.write_text("X")
        with pytest.raises(ValueError, match="wizard data dir"):
            _render(wizard_tree, cert_path=outside)

    def test_rejects_key_outside_data_dir(self, wizard_tree, tmp_path):
        outside = tmp_path / "elsewhere" / "key.pem"
        outside.parent.mkdir()
        outside.write_text("X")
        with pytest.raises(ValueError, match="wizard data dir"):
            _render(wizard_tree, key_path=outside)

    def test_rejects_path_traversal_token(self, wizard_tree):
        with pytest.raises(ValueError, match="traversal"):
            _render(wizard_tree, cert_path="..\\..\\evil.pem")

    def test_rejects_null_byte_in_path(self, wizard_tree):
        with pytest.raises(ValueError, match="null"):
            _render(wizard_tree, cert_path="cert\x00.pem")

    def test_rejects_empty_path(self, wizard_tree):
        with pytest.raises(ValueError, match="empty"):
            _render(wizard_tree, cert_path="")

    def test_rejects_non_string_path(self, wizard_tree):
        with pytest.raises(ValueError, match="filesystem path"):
            _render(wizard_tree, cert_path=123)


# ---------------------------------------------------------------------
# Smoke: rendered output is syntactically plausible
# ---------------------------------------------------------------------

class TestSmoke:

    def test_renders_to_string(self, wizard_tree):
        out = _render(wizard_tree)
        assert isinstance(out, str)
        assert out.strip()

    def test_includes_top_level_block(self, wizard_tree):
        out = _render(wizard_tree)
        # Global options come first; per-site blocks follow.
        assert "{\n    admin off" in out

    def test_keyword_only_args(self, wizard_tree):
        # Positional invocation MUST raise — the renderer's signature is
        # keyword-only to prevent argument-order accidents.
        with pytest.raises(TypeError):
            render_wizard_caddyfile(
                8100, 8099,
                wizard_tree["cert_path"],
                wizard_tree["key_path"],
                wizard_tree["data_dir"],
            )

    def test_path_resolution_is_canonical(self, wizard_tree):
        """Cert path is canonicalised via ``Path.resolve`` before rendering."""
        # Pass a relative-ish form via Path; the renderer should still
        # validate and substitute the resolved form.
        cert = Path(wizard_tree["cert_path"])
        out = _render(wizard_tree, cert_path=cert)
        assert str(cert.resolve()) in out
