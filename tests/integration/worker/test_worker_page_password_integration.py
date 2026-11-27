# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker page's control actions and its password (setup-rebuild FR-WK-4, FR-SEC-8, FR-DK-6).

Real parts: the worker WSGI app, ``web_ui.auth`` with its failure limit and
cache, ``password_hash`` and the worker's real ``config.json`` in a scratch
data folder. The hash is written straight into ``config.json``, the way
``--setup-apply`` (another process) writes it. The limit, cache and
semaphore paths are unit cases.

FR-DK-6 runs the real agent (``run_worker.py``) as a child with a scratch
home and settings folder (``WorkerCli``): the password from the environment
must reach the running page, and a bad one must stop the start with exit 5.
The rules, sources, file reading and save failures are unit cases
(``ui_password_env``).
"""
import json
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

from sethlans_worker_agent import config_store, enrollment_start, job_processor, ui_password_env
from sethlans_worker_agent.config_store import io as config_store_io
from sethlans_worker_agent.web_ui import auth, password_hash, password_rules
from shared.frozen_paths import get_caddy_path
from tests.integration._worker_cli import TIMEOUT, WorkerCli, free_ports

from . import _card_pages_harness as pages

PASSWORD = 'harbor-Violet-2741'
PAUSE = '/api/control/pause'


def test_control_actions_are_refused_until_a_password_is_set(monkeypatch, tmp_path):
    """FR-WK-4: no stored hash -> 403 "run Reset setup"; FR-SEC-8: after one is set, right Bearer 200, wrong 401."""
    monkeypatch.setenv('SETHLANS_WORKER_DATA_DIR', str(tmp_path / 'worker-data'))
    monkeypatch.setattr(config_store_io, 'SYSTEM_CONFIG_PATH', None)
    pages.bind_shutdown_event()
    try:
        assert pages.call(PAUSE, 'POST', {}, pages.bearer(PASSWORD)) == (
            '403 Forbidden', {'error': 'No worker page password is set; run Reset setup'})
        assert not job_processor.is_paused()

        config_store.set_many([(auth.HASH_KEY, password_hash.make(PASSWORD))])

        assert pages.call(PAUSE, 'POST', {}, pages.bearer('not-the-password')) == (
            '401 Unauthorized', {'error': 'Unauthorized'})
        assert not job_processor.is_paused()
        assert pages.call(PAUSE, 'POST', {}, pages.bearer(PASSWORD)) == ('200 OK', {'status': 'paused'})
        assert job_processor.is_paused()
    finally:
        pages.unbind_shutdown_event()


def _post(port, path, password):
    conn = HTTPConnection('127.0.0.1', port, timeout=30)
    try:
        headers = {'Authorization': f'Bearer {password}', 'Content-Type': 'application/json'}
        conn.request('POST', path, body=b'{}', headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def _wait_until_waiting_for_setup(cli, proc):
    """Poll (deadline) until the agent says it waits for setup: TLS, Waitress and Caddy are up by then."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"the agent exited {proc.returncode} before it was up\n{cli.agent_text()}")
        if enrollment_start.NOT_ENROLLED_TEXT in cli.log_text():
            return
        time.sleep(0.2)
    pytest.fail(f"the agent did not reach the wait for setup in {TIMEOUT} s\n{cli.agent_text()}")


def test_agent_start_takes_the_page_password_from_the_password_file(tmp_path):
    """FR-DK-6: a real agent start with SETHLANS_WORKER_UI_PASSWORD_FILE: the page takes it, only its hash is kept."""
    if not Path(get_caddy_path()).is_file():
        pytest.skip(f"Caddy binary not found at {get_caddy_path()}")
    cli = WorkerCli(tmp_path)
    secret = tmp_path / 'worker_ui_password'
    secret.write_bytes(PASSWORD.encode('utf-8') + b'\n')  # a secret file ends with a line end
    public, loopback, upstream = free_ports(3)
    proc = cli.start_agent({
        ui_password_env.PASSWORD_FILE_ENV: str(secret),
        'SETHLANS_WORKER_UI_PORT': str(public),
        'SETHLANS_WORKER_CADDY_PUBLIC_TLS_PORT': str(public),
        'SETHLANS_WORKER_CADDY_LOOPBACK_PORT': str(loopback),
        'SETHLANS_WORKER_WAITRESS_UPSTREAM_PORT': str(upstream),
    })
    try:
        _wait_until_waiting_for_setup(cli, proc)
        assert _post(upstream, PAUSE, 'not-the-password') == (401, {'error': 'Unauthorized'})
        assert _post(upstream, PAUSE, PASSWORD) == (200, {'status': 'paused'})
    finally:
        WorkerCli.stop_agent(proc)

    stored = cli.config_path.read_text(encoding='utf-8')
    assert json.loads(stored)['worker']['ui_password_hash'].startswith('pbkdf2_sha256$')
    assert PASSWORD not in stored
    output = cli.log_text() + cli.agent_text()
    assert PASSWORD not in output
    assert ui_password_env.PASSWORD_FILE_ENV in output
    assert 'No worker page password is set' not in output  # taken before the page started


def test_agent_start_with_a_password_that_breaks_the_rules_exits_5(tmp_path):
    """FR-DK-6: a real agent start with a bad SETHLANS_WORKER_UI_PASSWORD stops with exit 5 and the reason only."""
    bad = 'Zq7#k'
    cli = WorkerCli(tmp_path)
    proc = cli.start_agent({ui_password_env.PASSWORD_ENV: bad})
    try:
        assert proc.wait(timeout=TIMEOUT) == ui_password_env.UI_PASSWORD_FAILED
    finally:
        WorkerCli.stop_agent(proc)

    output = cli.log_text() + cli.agent_text()
    assert password_rules.TOO_SHORT in output
    assert bad not in output
    assert not cli.config_path.exists() or 'ui_password_hash' not in cli.config_path.read_text(encoding='utf-8')
