# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``GET /api/wizard/resume-target/`` — resume-route lookup (FR-CHK3-RESUME).

Reads ``.setup_progress.json`` (under the FR-CHK1a progress-file lock,
with the FR-CHK3-RESUME Windows transient-PermissionError retry) and
walks ``CHECKPOINT_NAMES`` forward to find the first incomplete
checkpoint. Returns the route the frontend SHOULD navigate to next via
``RESUME_NEXT_ROUTE``.

Topology gate: when ``topology.json`` reports ``manager`` (no co-located
worker) the ``worker_password_set`` checkpoint is treated as
auto-satisfied so the resume walker skips it.

Auth: requires a valid ``X-Wizard-Session`` header — the endpoint is
called immediately after a successful re-auth.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Iterable, Optional

from wizard.sethlans_wizard import progress
from wizard.sethlans_wizard.checkpoints import (
    CHECKPOINT_NAMES,
    MANAGER_WORKER_ONLY_CHECKPOINTS,
    RESUME_NEXT_ROUTE,
    VERIFIED,
)
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid
from wizard.sethlans_wizard.handlers.topology import TOPOLOGY_FILENAME

logger = logging.getLogger(__name__)


def _read_topology(data_dir: Path) -> Optional[str]:
    target = data_dir / TOPOLOGY_FILENAME
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read topology %s: %s", target, exc)
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    topo = payload.get("topology")
    return topo if isinstance(topo, str) else None


def compute_resume_route(data_dir: Path) -> str:
    """Walk checkpoints forward; return the route for the first incomplete step.

    Pre-Welcome (no checkpoints recorded yet) → ``"/"`` (welcome page).
    All checkpoints recorded → ``RESUME_NEXT_ROUTE[VERIFIED]`` (``/done``).

    Topology gate: when topology is ``manager`` (no co-located worker),
    ``worker_password_set`` is treated as auto-satisfied so the walker
    skips both the checkpoint AND its dedicated route.
    """
    payload = progress.read_checkpoints(data_dir)
    seen = []
    if isinstance(payload, dict):
        raw_seen = payload.get("checkpoints")
        if isinstance(raw_seen, list):
            seen = [n for n in raw_seen if isinstance(n, str)]
    seen_set = set(seen)
    topology = _read_topology(data_dir)
    is_manager_only = topology == "manager"

    # Build a per-topology route map: when manager-only, jump from
    # admin_validated straight to /verify, skipping /worker-password.
    route_map = dict(RESUME_NEXT_ROUTE)
    if is_manager_only:
        # The route after admin_validated normally points to
        # /worker-password; for manager-only it points to /verify
        # (the WORKER_PASSWORD_SET successor).
        route_map["admin_validated"] = RESUME_NEXT_ROUTE["worker_password_set"]

    last_completed: Optional[str] = None
    for name in CHECKPOINT_NAMES:
        if is_manager_only and name in MANAGER_WORKER_ONLY_CHECKPOINTS:
            continue
        if name in seen_set:
            last_completed = name
            continue
        if last_completed is None:
            return "/"
        return route_map.get(last_completed, "/")
    # Everything done.
    return route_map.get(VERIFIED, "/done")


def make_resume_target_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-CHK3-RESUME."""
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir)

    return handler


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "GET":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "GET")],
        )
    if _wsgi.query_string_has_forbidden_key(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "session token must not appear in URL"},
            status=400,
        )
    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )
    try:
        route = compute_resume_route(data_dir)
        topology = _read_topology(data_dir)
    except OSError as exc:
        logger.error("Could not compute resume route: %s", exc)
        return _wsgi.send_json(
            start_response,
            {"error": "could not read progress"},
            status=500,
        )
    return _wsgi.send_json(
        start_response,
        {"route": route, "topology": topology},
        status=200,
    )


__all__ = ["compute_resume_route", "make_resume_target_handler"]
