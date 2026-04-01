# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
# worker/tests/test_web_ui_http_integration.py
"""
Integration tests for the worker web UI HTTP server.

Starts a real ThreadingHTTPServer on a random port, makes real HTTP
requests via urllib, and verifies responses. Tests server lifecycle,
authentication, pause/resume, config mutation, body size limits,
and force mode mutual exclusion through the live HTTP stack.
"""

import json
import socket
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import pytest

from sethlans_worker_agent import config, job_processor, blender_executor
from sethlans_worker_agent.web_ui import auth
from sethlans_worker_agent.web_ui.server import WorkerRequestHandler


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _clear_state():
    job_processor._pause_event.clear()
    job_processor._active_jobs.clear()
    job_processor._gpu_assignment_map.clear()
    with job_processor._recent_jobs_lock:
        job_processor._recent_jobs.clear()
    with blender_executor._output_lock:
        blender_executor._last_output_lines.clear()


@pytest.fixture(autouse=True)
def reset_web_ui_state():
    """Reset module-level state to prevent cross-test pollution."""
    saved = (auth._token, config.JOB_POLLING_INTERVAL_SECONDS,
             config.HEARTBEAT_INTERVAL_SECONDS, config.FORCE_CPU_ONLY,
             config.FORCE_GPU_ONLY, config.GPU_SPLIT_MODE)
    _clear_state()
    yield
    (auth._token, config.JOB_POLLING_INTERVAL_SECONDS,
     config.HEARTBEAT_INTERVAL_SECONDS, config.FORCE_CPU_ONLY,
     config.FORCE_GPU_ONLY, config.GPU_SPLIT_MODE) = saved
    _clear_state()


@pytest.fixture
def web_ui_server(mocker):
    """Start a real HTTP server on a random port with a known token."""
    token = 'integration-test-token-abc123'
    auth._token = token
    for attr, val in [('HOSTNAME', 'test-host'), ('IP_ADDRESS', '10.0.0.1'),
                      ('OS_INFO', 'TestOS')]:
        mocker.patch(f'sethlans_worker_agent.web_ui.status.{attr}', val)
    mocker.patch('sethlans_worker_agent.system_monitor.WORKER_ID', 1)
    mocker.patch('sethlans_worker_agent.web_ui.status.get_gpu_device_details',
                 return_value=[])
    mocker.patch('sethlans_worker_agent.web_ui.status.get_cpu_thread_count',
                 return_value=8)
    mocker.patch('sethlans_worker_agent.web_ui.status.tool_manager_instance'
                 ).scan_for_local_blenders.return_value = []

    port = _free_port()
    srv = ThreadingHTTPServer(('127.0.0.1', port), WorkerRequestHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{port}', token
    srv.shutdown()


def _post(url, token=None, data=None):
    """POST helper returning (status_code, parsed_json_or_bytes)."""
    body = json.dumps(data).encode() if data else b''
    hdrs = {'Content-Type': 'application/json',
            'Content-Length': str(len(body))}
    if token:
        hdrs['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=body, headers=hdrs, method='POST')
    try:
        resp = urllib.request.urlopen(req)
        raw = resp.read()
        return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return e.code, raw


def _get(url):
    """GET helper returning (status_code, body_bytes)."""
    try:
        resp = urllib.request.urlopen(url)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestHttpServerLifecycle:
    """Real HTTP server starts, responds, and stops."""

    def test_get_root_returns_html(self, web_ui_server):
        base_url, _ = web_ui_server
        status, body = _get(f'{base_url}/')
        assert status == 200
        assert b'<html' in body.lower() or b'<!doctype' in body.lower()

    def test_get_api_status_returns_json_with_all_sections(self, web_ui_server):
        base_url, _ = web_ui_server
        status, body = _get(f'{base_url}/api/status')
        assert status == 200
        data = json.loads(body)
        expected = (
            'worker', 'hardware', 'config', 'active_jobs',
            'gpu_allocation', 'recent_jobs', 'tools',
        )
        for key in expected:
            assert key in data

    def test_get_unknown_path_returns_404(self, web_ui_server):
        base_url, _ = web_ui_server
        status, _ = _get(f'{base_url}/nonexistent')
        assert status == 404


class TestHttpAuthentication:
    """Auth enforcement on control endpoints via real HTTP."""

    def test_no_auth_returns_401(self, web_ui_server):
        base_url, _ = web_ui_server
        status, _ = _post(f'{base_url}/api/control/pause')
        assert status == 401

    def test_wrong_token_returns_401(self, web_ui_server):
        base_url, _ = web_ui_server
        status, _ = _post(f'{base_url}/api/control/pause', token='wrong')
        assert status == 401

    def test_valid_token_pause_returns_200(self, web_ui_server):
        base_url, token = web_ui_server
        status, body = _post(f'{base_url}/api/control/pause', token=token)
        assert status == 200
        assert body['status'] == 'paused'

    def test_valid_token_resume_returns_200(self, web_ui_server):
        base_url, token = web_ui_server
        status, body = _post(f'{base_url}/api/control/resume', token=token)
        assert status == 200
        assert body['status'] == 'resumed'


class TestHttpPauseResume:
    """Pause/resume side effects through real HTTP requests."""

    def test_pause_sets_paused_state(self, web_ui_server):
        base_url, token = web_ui_server
        assert not job_processor.is_paused()
        _post(f'{base_url}/api/control/pause', token=token)
        assert job_processor.is_paused() is True

    def test_resume_clears_paused_state(self, web_ui_server):
        base_url, token = web_ui_server
        job_processor.pause()
        _post(f'{base_url}/api/control/resume', token=token)
        assert job_processor.is_paused() is False

    def test_pause_resume_cycle(self, web_ui_server):
        base_url, token = web_ui_server
        assert not job_processor.is_paused()
        _post(f'{base_url}/api/control/pause', token=token)
        assert job_processor.is_paused()
        _post(f'{base_url}/api/control/resume', token=token)
        assert not job_processor.is_paused()


class TestHttpBodySizeLimit:
    """4KB request body size limit via real HTTP."""

    def test_post_body_over_4kb_returns_413(self, web_ui_server):
        base_url, token = web_ui_server
        big_data = {'key': 'POLLING_INTERVAL', 'value': 'x' * 5000}
        status, _ = _post(
            f'{base_url}/api/control/update', token=token, data=big_data
        )
        assert status == 413


class TestHttpConfigMutation:
    """Config changes through real HTTP requests."""

    def test_update_polling_interval(self, web_ui_server):
        base_url, token = web_ui_server
        status, body = _post(
            f'{base_url}/api/control/update', token=token,
            data={'key': 'POLLING_INTERVAL', 'value': 42}
        )
        assert status == 200
        assert body['status'] == 'updated'
        assert config.JOB_POLLING_INTERVAL_SECONDS == 42

    def test_disallowed_config_key_returns_403(self, web_ui_server):
        base_url, token = web_ui_server
        status, body = _post(
            f'{base_url}/api/control/update', token=token,
            data={'key': 'MANAGER_HOST', 'value': 'evil.com'}
        )
        assert status == 403
        assert 'not modifiable' in body['error']


class TestHttpForceModeExclusion:
    """Force mode mutual exclusion through real HTTP."""

    def test_force_gpu_clears_force_cpu(self, web_ui_server):
        base_url, token = web_ui_server
        config.FORCE_CPU_ONLY = False
        config.FORCE_GPU_ONLY = False
        _post(f'{base_url}/api/control/update', token=token,
              data={'key': 'FORCE_CPU_ONLY', 'value': True})
        assert config.FORCE_CPU_ONLY is True
        _post(f'{base_url}/api/control/update', token=token,
              data={'key': 'FORCE_GPU_ONLY', 'value': True})
        assert config.FORCE_GPU_ONLY is True
        assert config.FORCE_CPU_ONLY is False

    def test_force_cpu_clears_force_gpu(self, web_ui_server):
        base_url, token = web_ui_server
        config.FORCE_CPU_ONLY = False
        config.FORCE_GPU_ONLY = False
        _post(f'{base_url}/api/control/update', token=token,
              data={'key': 'FORCE_GPU_ONLY', 'value': True})
        assert config.FORCE_GPU_ONLY is True
        _post(f'{base_url}/api/control/update', token=token,
              data={'key': 'FORCE_CPU_ONLY', 'value': True})
        assert config.FORCE_CPU_ONLY is True
        assert config.FORCE_GPU_ONLY is False
