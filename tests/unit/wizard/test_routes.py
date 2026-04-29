# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coverage expansion: ``wizard/sethlans_wizard/routes.py``.

Verifies every Phase 1 step handler is registered via dispatching a
GET probe at each registered path and asserting the router does NOT
return its 404 envelope. The only exception is the FFmpeg progress
prefix mount — we dispatch a probe with a tail task_id segment to
prove the prefix-mode registration.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from wizard.sethlans_wizard import routes
from wizard.sethlans_wizard.router import Router


def _dispatch(router, path):
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(router.dispatch({
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }, start_response))
    return captured["status"], body


@pytest.fixture
def populated_router(tmp_path):
    static_root = tmp_path / "static"
    static_root.mkdir()
    for sub in ("vendor", "css", "js"):
        (static_root / sub).mkdir()
    # Provide a stub index file so the index handler doesn't 404.
    (static_root / "index.html").write_text("<html></html>", encoding="utf-8")
    (static_root / "topology.html").write_text("<html></html>", encoding="utf-8")
    (static_root / "redirecting.html").write_text("<html></html>", encoding="utf-8")
    r = Router()
    routes.register_routes(
        r,
        data_dir=tmp_path,
        setup_token=b"setup-token",
        ipc_secret=b"ipc-secret",
        wizard_port=8765,
        static_root=static_root,
    )
    return r


def _is_routed(status, body):
    """A registered route returns SOMETHING — possibly 405/401 — but
    NOT the router's 404 envelope ``{"error": "Not Found"}``."""
    if status.startswith("404"):
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return True
        return payload.get("error") != "Not Found"
    return True


class TestPhase1Routes:

    @pytest.mark.parametrize(
        "path",
        [
            "/api/health/",
            "/api/wizard/auth/",
            "/api/wizard/topology/",
            "/api/wizard/done/",
            "/api/wizard/runtime-ready/",
            "/api/wizard/launcher-log-path/",
            "/api/wizard/network/",
            "/api/wizard/database/",
            "/api/wizard/admin-user/",
            "/api/wizard/worker-password/",
            "/api/wizard/ffmpeg/start/",
            "/api/wizard/ffmpeg/cancel/",
            "/api/wizard/verify/",
            "/api/wizard/pending-setup/",
        ],
    )
    def test_route_resolved(self, populated_router, path):
        # Coverage expansion: each Phase 1 endpoint MUST be wired —
        # dispatching a request must not hit the router's 404.
        status, body = _dispatch(populated_router, path)
        assert _is_routed(status, body), (
            f"Route {path} returned router 404; not registered"
        )

    def test_progress_prefix_mount_resolves_for_arbitrary_task(
        self, populated_router,
    ):
        # The progress route is registered with exact=False so a tail
        # path component (the task_id) routes through it.
        status, body = _dispatch(
            populated_router, "/api/wizard/ffmpeg/progress/abc123",
        )
        assert _is_routed(status, body)


class TestStaticMounts:

    @pytest.mark.parametrize("sub", ["vendor", "css", "js"])
    def test_static_subdir_mount_registered(self, populated_router, sub):
        # The static handler returns its own 404 envelope (identical to
        # the router's) when files are missing — so probe registration
        # via the router's internal table instead.
        prefixes = [r[0] for r in populated_router._routes]
        assert f"/static/{sub}/" in prefixes

    def test_static_mounts_use_prefix_mode(self, populated_router):
        # FR-W-FE2 — static routes register with exact=False so a tail
        # path component (the actual asset filename) routes through.
        for prefix, _, exact in populated_router._routes:
            if prefix.startswith("/static/"):
                assert exact is False, (
                    f"static mount {prefix} must be prefix-mode"
                )

    def test_static_mounts_register_after_api_routes(
        self, populated_router,
    ):
        # First-match-wins ordering invariant: any /api/ route MUST be
        # registered before any /static/ route so the static prefix
        # cannot shadow an API endpoint.
        api_indexes = [
            i for i, (p, _, _) in enumerate(populated_router._routes)
            if p.startswith("/api/")
        ]
        static_indexes = [
            i for i, (p, _, _) in enumerate(populated_router._routes)
            if p.startswith("/static/")
        ]
        assert max(api_indexes) < min(static_indexes)

    def test_progress_prefix_after_exact_start_and_cancel(
        self, populated_router,
    ):
        # First-match-wins: progress (prefix) MUST register AFTER
        # start/cancel (exact) so a request to /api/wizard/ffmpeg/start/
        # never gets caught by the progress prefix mount.
        prefixes = [r[0] for r in populated_router._routes]
        i_start = prefixes.index("/api/wizard/ffmpeg/start/")
        i_cancel = prefixes.index("/api/wizard/ffmpeg/cancel/")
        i_progress = prefixes.index("/api/wizard/ffmpeg/progress/")
        assert i_start < i_progress
        assert i_cancel < i_progress

    def test_root_index_handler_registered(self, populated_router):
        status, body = _dispatch(populated_router, "/")
        assert _is_routed(status, body)

    def test_topology_page_registered(self, populated_router):
        status, body = _dispatch(populated_router, "/topology")
        assert _is_routed(status, body)

    def test_redirecting_page_registered(self, populated_router):
        status, body = _dispatch(populated_router, "/redirecting")
        assert _is_routed(status, body)


class TestRegistrationContract:

    def test_register_routes_accepts_pathlib_data_dir(self, tmp_path):
        # Coverage expansion: the helper accepts a Path and threads it
        # through every handler factory without crashing.
        static_root = tmp_path / "static"
        static_root.mkdir()
        for sub in ("vendor", "css", "js"):
            (static_root / sub).mkdir()
        (static_root / "index.html").write_text("ok", encoding="utf-8")
        (static_root / "topology.html").write_text("ok", encoding="utf-8")
        (static_root / "redirecting.html").write_text("ok", encoding="utf-8")
        r = Router()
        routes.register_routes(
            r,
            data_dir=Path(tmp_path),
            setup_token=b"x",
            ipc_secret=b"y",
            wizard_port=1,
            static_root=static_root,
        )
        # API + static both routable — at minimum the health endpoint
        # returns a 200.
        status, _ = _dispatch(r, "/api/health/")
        assert status.startswith("200")


class TestExports:

    def test_register_routes_exported(self):
        assert "register_routes" in routes.__all__
