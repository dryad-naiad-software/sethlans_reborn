# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Route registration completeness + topology-page does NOT fire ``/done/``.

Covers integration-test agent's mandatory scenarios 8 and 9.

* **Topology handler is not coupled to ``/done/``** — the
  production handler at ``/api/wizard/topology/`` writes
  ``topology.json`` but does NOT write the ``.wizard_done`` HMAC
  marker. The legacy spec's HIGH-1 fix put the ``/done/`` POST in the
  frontend ``js/topology.js`` controller; the new spec moves Continue
  navigation to ``/network``. Phase 1's task is the regression test
  that protects the server-side: a single
  ``POST /api/wizard/topology/`` MUST NOT result in a
  ``.wizard_done`` marker appearing on disk.
* **Every Phase 1 route is registered** — walk the wizard's actual
  router and assert each FR-M2-* endpoint from the spec's endpoint
  table is present.

This file uses the WSGI factory directly via ``server.create_app``
because route-table introspection is a structural property best
asserted without a subprocess. The "topology doesn't fire done"
assertion DOES use the live subprocess to prove the server-side
contract end-to-end.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wizard.sethlans_wizard import ipc
from wizard.sethlans_wizard.router import Router
from wizard.sethlans_wizard.routes import register_routes
from wizard.sethlans_wizard.server import create_app

from . import _http
from ._phase1_session import open_and_select, session_headers


# ---------------------- Topology no longer fires done ----------------------

def test_topology_handler_does_not_write_done_marker(wizard_process):
    """``POST /api/wizard/topology/`` writes topology.json — NOT .wizard_done.

    Regression guard for the spec change in
    ``setup-wizard-standalone-manager-migration.md`` Phase 1 scope:
    "Modify ... so Continue does NOT fire ``/api/wizard/done/``".
    The wizard subprocess's topology handler MUST keep its scope
    pinned to topology persistence.
    """
    wp = wizard_process
    open_and_select(wp, topology="manager")

    # topology.json was written; pending_setup writeable.
    assert (wp.data_dir / "topology.json").is_file()

    # The .wizard_done marker MUST NOT exist.
    marker_path = wp.wizard_subdir / ipc.MARKER_WIZARD_DONE
    assert not marker_path.exists(), (
        f"topology handler wrote {marker_path} — the spec change requires "
        "the JS frontend to fire /done/, not the server-side topology "
        "handler"
    )


def test_topology_then_subsequent_handlers_do_not_write_done(wizard_process):
    """Driving a Phase 1 sequence does not invoke ``/done/`` as a side-effect.

    None of network / database / admin-user / worker-password /
    verify / pending-setup handlers may write ``.wizard_done``;
    that is the exclusive domain of ``/api/wizard/done/`` itself.
    """
    wp = wizard_process
    session = open_and_select(wp, topology="manager_worker")
    headers = session_headers(session)

    # Hit a few handlers in sequence.
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()

    _http.post_json(
        f"{wp.base_url}/api/wizard/network/",
        {"bind_host": "127.0.0.1", "bind_port": free_port},
        headers=headers,
    )
    _http.post_json(
        f"{wp.base_url}/api/wizard/database/",
        {"engine": "sqlite", "name": "regression.db"},
        headers=headers,
    )
    _http.post_json(
        f"{wp.base_url}/api/wizard/admin-user/",
        {
            "username": "alice",
            "email": "alice@example.org",
            "password": "X9c!7Rq#Tv2pL@s",
            "password_confirm": "X9c!7Rq#Tv2pL@s",
        },
        headers=headers,
    )
    _http.post_json(
        f"{wp.base_url}/api/wizard/worker-password/",
        {"password": "longerThanEight!"},
        headers=headers,
    )
    # Tiny grace window for any sneaky background writer.
    time.sleep(0.2)

    marker_path = wp.wizard_subdir / ipc.MARKER_WIZARD_DONE
    assert not marker_path.exists(), (
        f"a Phase 1 handler wrote {marker_path} as a side-effect"
    )


# ---------------------- Route registration completeness ----------------------

# Endpoints the spec FR-M2-* table promises. A single source of truth
# for the registration test below.
_PHASE1_ROUTES_EXACT = (
    "/api/wizard/network/",          # FR-M2-3
    "/api/wizard/database/",         # FR-M2-4
    "/api/wizard/admin-user/",       # FR-M2-5
    "/api/wizard/worker-password/",  # FR-M2-6
    "/api/wizard/verify/",           # FR-M2-8
    "/api/wizard/pending-setup/",    # FR-PEND2
)
_SPEC1_KEPT_ROUTES = (
    "/api/wizard/auth/",
    "/api/wizard/topology/",
    "/api/wizard/done/",
    "/api/wizard/runtime-ready/",
    "/api/wizard/launcher-log-path/",
    "/api/health/",
)


def _build_router_for_introspection(tmp_path: Path) -> Router:
    """Build a Router populated by ``register_routes`` for introspection."""
    router = Router()
    register_routes(
        router,
        data_dir=tmp_path,
        setup_token=b"test-token",
        ipc_secret=b"test-secret",
        wizard_port=8099,
        static_root=tmp_path / "static",
    )
    return router


def test_every_phase1_route_is_registered(tmp_path):
    """Every FR-M2-* endpoint appears in the wizard's route table."""
    router = _build_router_for_introspection(tmp_path)
    registered = {prefix for prefix, _, _ in router._routes}

    missing = set(_PHASE1_ROUTES_EXACT) - registered
    assert not missing, f"Phase 1 routes missing: {missing}"


def test_spec1_routes_still_registered(tmp_path):
    """Spec 1's wizard endpoints survive Phase 1 refactor."""
    router = _build_router_for_introspection(tmp_path)
    registered = {prefix for prefix, _, _ in router._routes}
    missing = set(_SPEC1_KEPT_ROUTES) - registered
    assert not missing, f"Spec 1 routes regressed: {missing}"


def test_create_app_returns_callable_with_router_attached(tmp_path):
    """``create_app`` returns a WSGI callable with ``_router`` introspectable."""
    app = create_app(
        data_dir=tmp_path,
        setup_token=b"test-token",
        ipc_secret=b"test-secret",
        wizard_port=8099,
    )
    assert callable(app)
    assert hasattr(app, "_router")
    # Confirm Phase 1 routes are reachable through the app's router too.
    registered = {prefix for prefix, _, _ in app._router._routes}
    for r in _PHASE1_ROUTES_EXACT:
        assert r in registered, r


# Keep pytest happy with the side-effect import on POSIX-only branches.
_ = pytest
