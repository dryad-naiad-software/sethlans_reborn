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

    def test_returns_200(self, mocker, tmp_path):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
        resp = _invoke()
        assert resp.status_code == 200

    def test_contains_required_fields(self, mocker, tmp_path):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
        resp = _invoke()
        body = resp.data
        assert set(body.keys()) == {
            "boot_id", "version", "setup_mode",
            "workers_online", "jobs_queued", "jobs_rendering",
        }

    def test_no_redundant_state_field(self, mocker, tmp_path):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
        resp = _invoke()
        assert "state" not in resp.data

    def test_setup_mode_true_when_sentinel_absent(self, mocker, tmp_path):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
        # tmp_path has no .setup_complete
        resp = _invoke()
        assert resp.data["setup_mode"] is True

    def test_setup_mode_false_when_sentinel_present(
        self, mocker, tmp_path,
    ):
        from workers.services.sentinel import create_sentinel
        create_sentinel(tmp_path, "manager", ["verified"])
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
        resp = _invoke()
        assert resp.data["setup_mode"] is False

    def test_boot_id_matches_runtime_state(self, mocker, tmp_path):
        from sethlans_manager import runtime_state
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
        resp = _invoke()
        assert resp.data["boot_id"] == (
            runtime_state.manager_boot_id or ""
        )


# ------------------------------------------------------------------
# DB count query filters (FR-22c: status__in=[IDLE, RENDERING])
# ------------------------------------------------------------------

class TestDbQueries:

    def test_workers_online_filters_idle_and_rendering(
        self, mocker, tmp_path,
    ):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
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

    def test_jobs_queued_uses_queued_status(self, mocker, tmp_path):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
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

    def test_second_call_within_window_uses_cache(
        self, mocker, tmp_path,
    ):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
        w_spy = mocker.patch.object(
            view_mod, "_workers_online", return_value=7,
        )
        _invoke()
        _invoke()
        # Only one DB fetch — second call served from cache.
        assert w_spy.call_count == 1

    def test_cache_invalidates_on_boot_id_change(self, mocker, tmp_path):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
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

    def test_cache_expires_after_ttl(self, mocker, tmp_path):
        mocker.patch.object(
            view_mod, "_get_data_dir", return_value=tmp_path,
        )
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
