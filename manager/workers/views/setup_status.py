# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard status, topology, and network endpoints.

All views use ``@authentication_classes([])`` and
``@permission_classes([AllowAny])`` to bypass CSRF (matching the
``enroll_view`` pattern).  Each view checks the sentinel at the top
and returns 404 if setup is already complete.
"""

import json
import logging
import socket
from pathlib import Path

from django.conf import settings
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from shared.frozen_paths import get_data_dir, is_frozen
from workers.services.sentinel import (
    append_checkpoint,
    read_sentinel,
    write_sentinel,
)
from workers.services.setup import write_manager_ini

logger = logging.getLogger(__name__)

_VALID_TOPOLOGIES = ("manager", "manager_worker", "worker_only")


def _get_data_dir() -> Path:
    """Return the manager data directory."""
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


def _get_ini_path() -> Path:
    """Return path to manager.ini."""
    return _get_data_dir() / "manager.ini"


def _setup_complete() -> bool:
    """Return True if setup has fully completed (sentinel has completed_at)."""
    sentinel = read_sentinel(_get_data_dir())
    return sentinel is not None and sentinel.get("completed_at") is not None


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_status_view(request):
    """GET /api/setup/status/ (FR-A1).

    Returns the current setup state read from the sentinel file.
    """
    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)

    if sentinel and sentinel.get("completed_at"):
        return Response(status=404)

    if sentinel is None:
        return Response({
            "complete": False,
            "topology": None,
            "current_step": None,
            "checkpoints": [],
        })

    checkpoints = sentinel.get("checkpoints", [])
    topology = sentinel.get("topology")
    return Response({
        "complete": False,
        "topology": topology,
        "current_step": _infer_current_step(checkpoints, topology),
        "checkpoints": checkpoints,
    })


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_topology_view(request):
    """POST /api/setup/topology/ (FR-A2).

    Accepts ``{"topology": "manager"|"manager_worker"|"worker_only"}``.
    Last write wins; resets subsequent checkpoints.
    """
    if _setup_complete():
        return Response(status=404)

    topology = request.data.get("topology")
    if topology not in _VALID_TOPOLOGIES:
        return Response(
            {"error": f"Invalid topology. Must be one of: "
                      f"{', '.join(_VALID_TOPOLOGIES)}"},
            status=400,
        )

    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)
    if sentinel is None:
        sentinel = {
            "version": 1,
            "completed_at": None,
            "topology": topology,
            "checkpoints": ["topology_chosen"],
        }
    else:
        sentinel["topology"] = topology
        # Reset to only topology checkpoint (idempotency)
        sentinel["checkpoints"] = ["topology_chosen"]
    write_sentinel(data_dir, sentinel)

    # Also write topology.json for the launcher (FR-C3)
    topology_path = data_dir / "topology.json"
    topology_path.write_text(
        json.dumps({"topology": topology}, indent=2),
        encoding="utf-8",
    )

    return Response({"status": "ok"})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def setup_network_view(request):
    """POST /api/setup/network/ (FR-A3).

    Accepts ``{"bind_host": str, "bind_port": int}``.
    Validates port via trial ``socket.bind()``.
    """
    if _setup_complete():
        return Response(status=404)

    bind_host = request.data.get("bind_host", "0.0.0.0")
    bind_port = request.data.get("bind_port", 8080)

    try:
        bind_port = int(bind_port)
    except (TypeError, ValueError):
        return Response(
            {"error": "bind_port must be an integer"}, status=400,
        )

    # Trial socket bind to validate port availability
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((bind_host, bind_port))
    except OSError as exc:
        return Response(
            {"error": f"Port {bind_port} not available: {exc}"},
            status=400,
        )

    config_updates = {
        "server.host": bind_host,
        "server.port": str(bind_port),
    }

    data_dir_override = request.data.get("data_dir")
    if data_dir_override:
        config_updates["server.data_dir"] = data_dir_override

    write_manager_ini(config_updates, _get_ini_path())
    append_checkpoint(_get_data_dir(), "network_configured")

    return Response({
        "status": "ok",
        "bind_host": bind_host,
        "bind_port": bind_port,
    })


def _infer_current_step(
    checkpoints: list[str], topology: str | None,
) -> str | None:
    """Determine which wizard step should be shown next."""
    steps = [
        "topology_chosen",
        "network_configured",
        "database_configured",
        "admin_created",
    ]
    if topology == "manager_worker":
        steps.append("worker_password_set")
    steps.extend([
        "ffmpeg_installed",
    ])
    if topology == "manager_worker":
        steps.append("blender_predownloaded")
    steps.append("verified")

    for step in steps:
        if step not in checkpoints:
            return step
    return None
