# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``GET /api/status/public/`` (tray spec FR-22c).

The view is invoked directly (bypassing URL routing) because the path
is only registered on the loopback URLconf — the Django test client
mounts the main URLconf.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIRequestFactory

from workers.views import status_public as view_mod
from workers.views.status_public import status_public_view

pytestmark = pytest.mark.django_db

#: The full response keyset since issue #304 dropped ``setup_mode``.
EXPECTED_KEYS = {
    "boot_id", "version",
    "workers_online", "jobs_queued", "jobs_rendering",
}


@pytest.fixture(autouse=True)
def _clear_count_cache():
    view_mod._count_cache.clear()
    yield
    view_mod._count_cache.clear()


def _invoke():
    """Invoke status_public_view via DRF APIRequestFactory."""
    rf = APIRequestFactory()
    request = rf.get("/api/status/public/")
    return status_public_view(request)


# ------------------------------------------------------------------
# Response shape + required fields (FR-22c)
# ------------------------------------------------------------------

class TestResponseFields:

    def test_returns_200(self):
        resp = _invoke()
        assert resp.status_code == 200

    def test_keys_are_exactly_the_five_status_fields(self):
        """Issue #304: no ``setup_mode`` (or any other extra key)."""
        resp = _invoke()
        assert set(resp.data.keys()) == EXPECTED_KEYS

    def test_no_setup_mode_field(self):
        resp = _invoke()
        assert "setup_mode" not in resp.data

    def test_no_redundant_state_field(self):
        resp = _invoke()
        assert "state" not in resp.data

    def test_schema_does_not_document_setup_mode(self):
        """The OpenAPI response serializer lists the same five fields."""
        fields = set(view_mod.StatusPublicResponseSerializer.fields)
        assert fields == EXPECTED_KEYS

    def test_view_no_longer_reads_the_setup_marker(self):
        """Issue #304: the view has no marker-folder lookup at all, so a
        marker in the wrong folder cannot change its answer."""
        assert not hasattr(view_mod, "_get_data_dir")
        assert not hasattr(view_mod, "is_setup_mode")

    def test_boot_id_matches_runtime_state(self):
        from sethlans_manager import runtime_state
        resp = _invoke()
        assert resp.data["boot_id"] == (
            runtime_state.manager_boot_id or ""
        )


# ------------------------------------------------------------------
# DB count query filters (FR-22c: status__in=[IDLE, RENDERING])
# ------------------------------------------------------------------

class TestDbQueries:

    def test_workers_online_filters_idle_and_rendering(self, mocker):
        # Spy on Worker.objects.filter.
        from workers.models import Worker
        spy = mocker.spy(Worker.objects, "filter")
        _invoke()
        # Spy must have been called at least once for workers_online
        # with status__in=["IDLE", "RENDERING"].
        called_kwargs = [call.kwargs for call in spy.call_args_list]
        assert any(
            c.get("status__in") == ["IDLE", "RENDERING"]
            for c in called_kwargs
        )

    def test_jobs_queued_uses_queued_status(self, mocker):
        from workers.models import Job
        spy = mocker.spy(Job.objects, "filter")
        _invoke()
        called_kwargs = [call.kwargs for call in spy.call_args_list]
        assert any(c.get("status") == "QUEUED" for c in called_kwargs)
        assert any(c.get("status") == "RENDERING" for c in called_kwargs)


# ------------------------------------------------------------------
# In-process cache (FR-22c 2s TTL)
# ------------------------------------------------------------------

class TestCountCache:

    def test_second_call_within_window_uses_cache(self, mocker):
        w_spy = mocker.patch.object(
            view_mod, "_workers_online", return_value=7,
        )
        _invoke()
        _invoke()
        # Only one DB fetch — second call served from cache.
        assert w_spy.call_count == 1

    def test_cache_invalidates_on_boot_id_change(self, mocker):
        w_spy = mocker.patch.object(
            view_mod, "_workers_online", return_value=7,
        )
        import sethlans_manager.runtime_state as rt
        original = rt.manager_boot_id
        try:
            rt.manager_boot_id = "boot-A"
            _invoke()
            rt.manager_boot_id = "boot-B"
            _invoke()
            # Each boot_id yields its own cache entry.
            assert w_spy.call_count == 2
        finally:
            rt.manager_boot_id = original

    def test_cache_expires_after_ttl(self, mocker):
        w_spy = mocker.patch.object(
            view_mod, "_workers_online", return_value=7,
        )
        # Start time=100; after-TTL time=200.
        times = iter([100.0, 100.0, 100.0, 200.0, 200.0, 200.0])
        mocker.patch.object(
            view_mod.time, "monotonic", side_effect=lambda: next(times),
        )
        _invoke()
        _invoke()
        assert w_spy.call_count == 2
