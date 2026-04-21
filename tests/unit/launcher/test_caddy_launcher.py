# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`launcher.caddy_launcher`.

Covers:

- ``build_manager_caddy_supervisor`` constructs a CaddySupervisor
  with the manager-flavoured template kwargs and env overlay.
- ``apply_restart_request`` forwards the request payload onto
  ``update_template_kwargs`` and calls ``restart``.

The underlying Caddy subprocess is never spawned — we validate
constructor wiring and the restart-request forwarding path only.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from launcher import caddy_launcher as cl_mod


@pytest.fixture
def manager_tree(tmp_path):
    data_dir = tmp_path / 'manager_data'
    (data_dir / 'tls').mkdir(parents=True)
    cert = data_dir / 'tls' / 'c.crt'
    key = data_dir / 'tls' / 'c.key'
    cert.write_text('CERT')
    key.write_text('KEY')
    binary = tmp_path / 'caddy_stub'
    binary.write_text('#!/bin/sh\nexit 0\n')
    try:
        binary.chmod(0o755)
    except OSError:
        pass
    return {
        'data_dir': data_dir,
        'cert': cert,
        'key': key,
        'binary': binary,
        'caddyfile': data_dir / 'caddy' / 'Caddyfile',
    }


def test_build_manager_caddy_supervisor_wires_renderer_and_overlay(
    manager_tree,
):
    sv = cl_mod.build_manager_caddy_supervisor(
        caddyfile_path=manager_tree['caddyfile'],
        public_tls_port=8080,
        loopback_plaintext_port=8089,
        uvicorn_upstream_port=18080,
        waitress_loopback_upstream_port=18088,
        cert_path=manager_tree['cert'],
        key_path=manager_tree['key'],
        manager_data_dir=manager_tree['data_dir'],
        binary_path=manager_tree['binary'],
    )
    assert sv is not None
    # The supervisor carries the manager env overlay mapping and the
    # manager caddyfile path env.
    assert sv._caddyfile_path_env == (
        "SETHLANS_MANAGER_CADDYFILE_PATH"
    )
    assert sv._env_overlay_mapping[
        "public_tls_port"
    ] == "SETHLANS_MANAGER_CADDY_PUBLIC_TLS_PORT"
    # Renderer is the manager-flavoured pure function.
    from sethlans_manager.caddy_template import render_manager_caddyfile
    assert sv._caddyfile_renderer is render_manager_caddyfile


def test_apply_restart_request_updates_kwargs_and_restarts():
    fake_sv = MagicMock()
    request = {
        "public_tls_port": 9999,
        "uvicorn_upstream_port": 19999,
    }
    cl_mod.apply_restart_request(fake_sv, request)
    fake_sv.update_template_kwargs.assert_called_once_with(**request)
    fake_sv.restart.assert_called_once()


def test_load_manager_renderer_returns_callable():
    """The renderer import helper returns the pure function."""
    renderer = cl_mod._load_manager_renderer()
    assert callable(renderer)
    from sethlans_manager.caddy_template import render_manager_caddyfile
    assert renderer is render_manager_caddyfile
