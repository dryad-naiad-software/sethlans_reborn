# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers for ``test_caddy_supervisor`` tests."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from sethlans_worker_agent.agent_caddy import (
    CADDYFILE_PATH_ENV,
    _WORKER_ENV_OVERLAY,
)
from sethlans_worker_agent.caddy_template import render_worker_caddyfile
from shared.caddy_supervisor import CaddySupervisor


def make_worker_tree(tmp_path) -> SimpleNamespace:
    """Build a realistic worker tree with data dir, cert/key, and stub binary."""
    data_dir = tmp_path / 'worker_data'
    (data_dir / 'tls').mkdir(parents=True)
    cert = data_dir / 'tls' / 'worker.crt'
    key = data_dir / 'tls' / 'worker.key'
    cert.write_text('CERT')
    key.write_text('KEY')

    binary = tmp_path / 'caddy_stub'
    binary.write_text('#!/bin/sh\nexit 0\n')
    try:
        binary.chmod(0o755)
    except OSError:
        pass

    return SimpleNamespace(
        data_dir=data_dir,
        cert=cert,
        key=key,
        binary=binary,
        caddyfile=data_dir / 'caddy' / 'Caddyfile',
    )


def make_supervisor(worker_tree) -> CaddySupervisor:
    """Construct a worker-flavoured CaddySupervisor with valid defaults."""
    template_kwargs = {
        'public_tls_port': 8443,
        'loopback_plaintext_port': 18443,
        'waitress_upstream_port': 28443,
        'cert_path': worker_tree.cert,
        'key_path': worker_tree.key,
        'worker_data_dir': worker_tree.data_dir,
    }
    return CaddySupervisor(
        binary_path=worker_tree.binary,
        caddyfile_path=worker_tree.caddyfile,
        caddyfile_renderer=render_worker_caddyfile,
        template_kwargs=template_kwargs,
        caddyfile_path_env=CADDYFILE_PATH_ENV,
        env_overlay_mapping=_WORKER_ENV_OVERLAY,
    )


class FakeProc:
    """Minimal ``subprocess.Popen`` stand-in for supervisor tests."""

    def __init__(self, pid: int = 12345, exit_code=None):
        self.pid = pid
        self._exit_code = exit_code
        self.killed = False
        self.signalled = []
        self.waits = 0

    def poll(self):
        return self._exit_code

    def set_exit(self, code):
        self._exit_code = code

    def wait(self, timeout=None):
        self.waits += 1
        if self._exit_code is None:
            raise subprocess.TimeoutExpired(cmd='caddy', timeout=timeout)
        return self._exit_code

    def send_signal_and_exit(self, sig):
        """For graceful-stop tests: record signal and mark exit."""
        self.signalled.append(sig)
        self._exit_code = 0

    def send_signal(self, sig):
        self.signalled.append(sig)

    def kill(self):
        self.killed = True
        self._exit_code = -9
