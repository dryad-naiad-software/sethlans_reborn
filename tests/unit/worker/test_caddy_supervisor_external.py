# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the external-Caddyfile branch of
:mod:`sethlans_worker_agent.caddy_supervisor` (Phase 5c).

When ``$SETHLANS_WORKER_CADDYFILE_PATH`` is set (Docker images), the
supervisor uses the supplied path verbatim and skips the native
template-and-write step. Caddy resolves ``{$VAR}`` placeholders from
its process environment, which the supervisor overlays onto the
child's env at spawn time.

Split from ``test_caddy_supervisor.py`` to keep each test module
under the project's 300-line ceiling.
"""

from __future__ import annotations

import os as _os
from unittest.mock import patch

import pytest

from sethlans_worker_agent.agent_caddy import CADDYFILE_PATH_ENV
from shared.caddy_supervisor import CaddyfileNotFoundError
from shared.caddy_supervisor import supervisor as sup_mod
from tests.unit.worker._caddy_supervisor_helpers import (
    FakeProc, make_supervisor, make_worker_tree,
)


@pytest.fixture
def worker_tree(tmp_path):
    return make_worker_tree(tmp_path)


def test_start_with_external_caddyfile_skips_template(
    worker_tree, monkeypatch, tmp_path,
):
    """Env var set → use supplied path verbatim, no template write."""
    external = tmp_path / 'prebaked_Caddyfile'
    external.write_text('# pre-baked static Caddyfile\n')
    monkeypatch.setenv(CADDYFILE_PATH_ENV, str(external))

    sv = make_supervisor(worker_tree)
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured['argv'] = argv
        captured['kwargs'] = kwargs
        return FakeProc()

    # If the native branch accidentally runs, it would create the
    # templated Caddyfile in worker_tree.caddyfile.
    assert not worker_tree.caddyfile.exists()

    with patch.object(
        sup_mod._proc.subprocess, 'Popen', side_effect=fake_popen,
    ):
        sv.start()

    # The external path is used verbatim as the --config arg.
    assert str(external) in captured['argv']
    # The native-template target was NOT written.
    assert not worker_tree.caddyfile.exists()
    # The external file was NOT rewritten (content unchanged).
    assert external.read_text() == '# pre-baked static Caddyfile\n'

    # Caddy spawn env includes the {$VAR} placeholder overlay.
    env = captured['kwargs'].get('env')
    assert env is not None
    assert env['SETHLANS_WORKER_CADDY_PUBLIC_TLS_PORT'] == '8443'
    assert env['SETHLANS_WORKER_CADDY_LOOPBACK_PORT'] == '18443'
    assert env['SETHLANS_WORKER_WAITRESS_UPSTREAM_PORT'] == '28443'
    assert env['SETHLANS_WORKER_CERT_PATH'] == str(worker_tree.cert)
    assert env['SETHLANS_WORKER_KEY_PATH'] == str(worker_tree.key)
    # Parent env is preserved (PATH etc.) — overlay is merged on top
    # of os.environ so Caddy still inherits standard process env.
    if 'PATH' in _os.environ:
        assert env.get('PATH') == _os.environ['PATH']

    sv.stop(timeout=0.1)


def test_start_external_caddyfile_missing_raises(
    worker_tree, monkeypatch, tmp_path,
):
    """Env var pointing at a non-existent file → clear error, no spawn."""
    missing = tmp_path / 'does_not_exist_Caddyfile'
    monkeypatch.setenv(CADDYFILE_PATH_ENV, str(missing))

    sv = make_supervisor(worker_tree)
    popen_called = {'n': 0}

    def fake_popen(argv, **kwargs):
        popen_called['n'] += 1
        return FakeProc()

    with patch.object(
        sup_mod._proc.subprocess, 'Popen', side_effect=fake_popen,
    ):
        with pytest.raises(CaddyfileNotFoundError):
            sv.start()

    assert popen_called['n'] == 0
    # Native template target also was not written.
    assert not worker_tree.caddyfile.exists()


def test_start_external_caddyfile_is_directory_raises(
    worker_tree, monkeypatch, tmp_path,
):
    """Env var pointing at a directory (not a file) → clear error."""
    a_dir = tmp_path / 'not_a_file_dir'
    a_dir.mkdir()
    monkeypatch.setenv(CADDYFILE_PATH_ENV, str(a_dir))

    sv = make_supervisor(worker_tree)
    popen_called = {'n': 0}

    def fake_popen(argv, **kwargs):
        popen_called['n'] += 1
        return FakeProc()

    with patch.object(
        sup_mod._proc.subprocess, 'Popen', side_effect=fake_popen,
    ):
        with pytest.raises(CaddyfileNotFoundError):
            sv.start()
    assert popen_called['n'] == 0


def test_start_without_env_uses_native_template(worker_tree, monkeypatch):
    """Env var unset → legacy template-and-write branch runs."""
    monkeypatch.delenv(CADDYFILE_PATH_ENV, raising=False)

    sv = make_supervisor(worker_tree)
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured['kwargs'] = kwargs
        return FakeProc()

    with patch.object(
        sup_mod._proc.subprocess, 'Popen', side_effect=fake_popen,
    ):
        sv.start()

    # Native branch: templated Caddyfile written at the supervisor's
    # configured path.
    assert worker_tree.caddyfile.is_file()
    content = worker_tree.caddyfile.read_text()
    assert ':8443 {' in content
    # No spawn-env overlay — the native branch inherits parent env
    # because the static-Caddyfile {$VAR} substitutions aren't needed.
    assert captured['kwargs'].get('env') is None

    sv.stop(timeout=0.1)
