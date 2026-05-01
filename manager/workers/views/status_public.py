# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Loopback-only public status endpoint for the tray helper.

Served ONLY by the internal-origin Waitress loopback listener
(``127.0.0.1:<internal_port>``), which pins ``request.urlconf`` to
``sethlans_manager.urls_loopback`` via ``UrlconfOriginMiddleware``.
The public-origin listener does NOT register this path; requests to
``/api/status/public/`` on the public listener return 404.

Network isolation (loopback-only socket binding) is the authorization
gate — the view itself has no auth classes and no permission check.
See ``development/specs/tray-helper-unified.md`` FR-22, FR-22a, FR-22c,
FR-24.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from sethlans_manager import __version__ as _MANAGER_VERSION
from sethlans_manager import runtime_state
from workers.services.data_dir import get_manager_data_dir as _get_data_dir
from workers.models import Job, Worker
from workers.services.sentinel import is_setup_mode

# In-process cache for the three DB COUNT(*) queries.  A long-lived
# tray client polls every 2s; multiple dashboard tabs would otherwise
# multiply the per-tick COUNT cost.  TTL collapses repeat calls within
# a 2-second window.  Cache auto-invalidates on manager restart because
# ``runtime_state.manager_boot_id`` changes.
_COUNT_CACHE_TTL_SECONDS = 2.0
_count_cache: dict[tuple[str, str], tuple[float, int]] = {}
_count_cache_lock = threading.Lock()


def _cached_count(key: str, fetch: Callable[[], int]) -> int:
    """Return a cached COUNT or compute-and-store if the entry is stale.

    Keyed by ``(manager_boot_id, key)`` so a restart (new boot_id)
    invalidates every entry for free.
    """
    boot_id = runtime_state.manager_boot_id or ""
    cache_key = (boot_id, key)
    now = time.monotonic()
    with _count_cache_lock:
        entry = _count_cache.get(cache_key)
        if entry is not None:
            stored_at, value = entry
            if (now - stored_at) < _COUNT_CACHE_TTL_SECONDS:
                return value
    # Miss or stale — compute outside the lock to avoid holding it
    # across DB I/O.
    value = fetch()
    with _count_cache_lock:
        _count_cache[cache_key] = (now, value)
    return value


def _workers_online() -> int:
    return Worker.objects.filter(
        status__in=["IDLE", "RENDERING"],
    ).count()


def _jobs_queued() -> int:
    return Job.objects.filter(status="QUEUED").count()


def _jobs_rendering() -> int:
    return Job.objects.filter(status="RENDERING").count()


StatusPublicResponseSerializer = inline_serializer(
    name="StatusPublicResponse",
    fields={
        "boot_id": serializers.CharField(
            help_text="Per-process UUID; changes on manager restart.",
        ),
        "version": serializers.CharField(
            help_text="Manager package version.",
        ),
        "setup_mode": serializers.BooleanField(
            help_text="True while the setup wizard has not completed.",
        ),
        "workers_online": serializers.IntegerField(
            help_text="Workers in IDLE or RENDERING state.",
        ),
        "jobs_queued": serializers.IntegerField(
            help_text="Jobs with status=QUEUED.",
        ),
        "jobs_rendering": serializers.IntegerField(
            help_text="Jobs with status=RENDERING.",
        ),
    },
)


@extend_schema(
    tags=["System"],
    summary="Tray-helper status payload (loopback only).",
    description=(
        "Served only by the manager's loopback listener on "
        "``127.0.0.1:<loopback_port>``.  Returns boot identity, "
        "version, setup mode, and lightweight operational counts.  "
        "DB counts are cached in-process for 2 seconds."
    ),
    responses={200: StatusPublicResponseSerializer},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def status_public_view(request):
    """Return the tray's status payload.

    No auth classes, no CSRF — reachability is gated at the socket layer
    (this path is registered only on the loopback URLconf, pinned by
    the urlconf-origin middleware on the internal-origin Waitress
    listener).
    """
    data_dir = _get_data_dir()
    payload = {
        "boot_id": runtime_state.manager_boot_id or "",
        "version": _MANAGER_VERSION,
        "setup_mode": is_setup_mode(data_dir),
        "workers_online": _cached_count(
            "workers_online", _workers_online,
        ),
        "jobs_queued": _cached_count("jobs_queued", _jobs_queued),
        "jobs_rendering": _cached_count(
            "jobs_rendering", _jobs_rendering,
        ),
    }
    return Response(payload)
