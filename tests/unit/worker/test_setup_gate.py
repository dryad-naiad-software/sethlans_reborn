# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/gate.py``.

Covers init_gate, is_in_setup_mode, mark_setup_complete, and the
setup_gate_wrapper ASGI middleware.  Module state is reset by the
autouse fixture in ``conftest.py``.
"""

import asyncio
import json

from sethlans_worker_agent.web_ui.setup import gate
from sethlans_worker_agent.web_ui.setup.gate import (
    init_gate,
    is_in_setup_mode,
    mark_setup_complete,
    setup_gate_wrapper,
)

from tests.unit.worker._asgi_helpers import (
    make_scope,
    make_receive,
    ResponseCollector,
)


# -------------------------------------------------------------------
# init_gate
# -------------------------------------------------------------------

class TestInitGate:
    def test_sets_complete_from_sentinel(self, tmp_path, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.gate.is_setup_complete",
            return_value=True,
        )
        init_gate(tmp_path)
        assert gate._setup_complete is True

    def test_sets_incomplete_when_no_sentinel(self, tmp_path, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.gate.is_setup_complete",
            return_value=False,
        )
        init_gate(tmp_path)
        assert gate._setup_complete is False


# -------------------------------------------------------------------
# is_in_setup_mode / mark_setup_complete
# -------------------------------------------------------------------

class TestSetupModeToggle:
    def test_defaults_to_setup_mode(self):
        assert is_in_setup_mode() is True

    def test_mark_complete_flips_gate(self):
        assert is_in_setup_mode() is True
        mark_setup_complete()
        assert is_in_setup_mode() is False


# -------------------------------------------------------------------
# setup_gate_wrapper
# -------------------------------------------------------------------

class TestSetupGateWrapper:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_passes_through_when_complete(self):
        mark_setup_complete()
        called = []

        async def inner(scope, receive, send):
            called.append(True)

        scope = make_scope(path='/dashboard')
        collector = ResponseCollector()
        self._run(
            setup_gate_wrapper(
                scope, make_receive(), collector, inner,
            )
        )
        assert len(called) == 1

    def test_blocks_non_setup_path_with_503_during_setup(self):
        scope = make_scope(path='/dashboard')
        collector = ResponseCollector()

        async def inner(scope, receive, send):
            raise AssertionError("inner should not be called")

        self._run(
            setup_gate_wrapper(
                scope, make_receive(), collector, inner,
            )
        )
        assert collector.status == 503
        body = json.loads(collector.body)
        assert body["detail"] == "Setup not complete."

    def test_allows_api_setup_path_during_setup(self):
        called = []

        async def inner(scope, receive, send):
            called.append(True)

        scope = make_scope(path='/api/setup/status/')
        collector = ResponseCollector()
        self._run(
            setup_gate_wrapper(
                scope, make_receive(), collector, inner,
            )
        )
        assert len(called) == 1

    def test_allows_setup_path_during_setup(self):
        called = []

        async def inner(scope, receive, send):
            called.append(True)

        scope = make_scope(path='/setup')
        collector = ResponseCollector()
        self._run(
            setup_gate_wrapper(
                scope, make_receive(), collector, inner,
            )
        )
        assert len(called) == 1

    def test_non_http_scope_passes_through_during_setup(self):
        called = []

        async def inner(scope, receive, send):
            called.append(True)

        scope = {'type': 'lifespan'}
        collector = ResponseCollector()
        self._run(
            setup_gate_wrapper(
                scope, make_receive(), collector, inner,
            )
        )
        assert len(called) == 1

    def test_blocks_api_non_setup_path(self):
        scope = make_scope(path='/api/jobs/')
        collector = ResponseCollector()

        async def inner(scope, receive, send):
            raise AssertionError("inner should not be called")

        self._run(
            setup_gate_wrapper(
                scope, make_receive(), collector, inner,
            )
        )
        assert collector.status == 503

    def test_allows_setup_subpath(self):
        called = []

        async def inner(scope, receive, send):
            called.append(True)

        scope = make_scope(path='/api/setup/worker/enroll/')
        collector = ResponseCollector()
        self._run(
            setup_gate_wrapper(
                scope, make_receive(), collector, inner,
            )
        )
        assert len(called) == 1
