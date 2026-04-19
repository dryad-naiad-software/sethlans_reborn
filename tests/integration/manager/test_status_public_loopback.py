# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``GET /api/status/public/`` — the tray helper's
loopback-only status endpoint (FR-22 / FR-22a / FR-22c in
``development/specs/tray-helper-unified.md``).

What these tests cover:

* The main listener (default ``ROOT_URLCONF = 'sethlans_manager.urls'``)
  does NOT register the path — probing it returns 404.
* The loopback listener's URL config
  (``sethlans_manager.urls_loopback``) serves the full payload.
* Response payload shape is exactly
  ``{boot_id, version, setup_mode, workers_online, jobs_queued,
  jobs_rendering}`` with no extra fields.
* ``workers_online`` counts only workers whose status is ``IDLE`` or
  ``RENDERING`` — ``OFFLINE`` workers are excluded.
* ``jobs_queued`` counts only ``QUEUED`` jobs; ``jobs_rendering`` only
  ``RENDERING``.
* ``setup_mode`` is ``True`` while the sentinel is absent and ``False``
  after it is written.
* The in-process 2 s cache collapses repeat DB COUNTs within a window;
  new data becomes visible after the TTL expires.
* Changing ``runtime_state.manager_boot_id`` (simulated restart)
  invalidates the cache regardless of time.
* ``uvicorn_launcher.launch`` wires the loopback listener to
  ``host='127.0.0.1'``; the socket-level loopback restriction is
  enforced at bind time, not in Python.
"""

from __future__ import annotations

import time
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from sethlans_manager import runtime_state
from workers.models import Job, Worker, SupportedBlenderVersion
from workers.views import status_public as status_public_mod

User = get_user_model()

_EXPECTED_KEYS = {
    "boot_id",
    "version",
    "setup_mode",
    "workers_online",
    "jobs_queued",
    "jobs_rendering",
}


def _clear_count_cache() -> None:
    with status_public_mod._count_cache_lock:
        status_public_mod._count_cache.clear()


@pytest.fixture(autouse=True)
def _isolate_count_cache():
    """Each test starts and ends with an empty status_public count cache.

    The module-level cache survives across tests; without this teardown a
    cache hit from a prior test would leak.
    """
    _clear_count_cache()
    prev_boot = runtime_state.manager_boot_id
    # Pin a known boot_id so tests can assert on it without depending on
    # whatever the runtime_init hook picked.
    runtime_state.manager_boot_id = uuid.uuid4().hex
    yield
    _clear_count_cache()
    runtime_state.manager_boot_id = prev_boot


def _make_worker(hostname: str, status: str) -> Worker:
    user = User.objects.create_user(username=f"w_{hostname}")
    user.set_unusable_password()
    user.save()
    return Worker.objects.create(
        hostname=hostname,
        user=user,
        is_active=True,
        status=status,
    )


def _make_job(asset, status: str) -> Job:
    return Job.objects.create(
        asset=asset,
        name=f"job-{uuid.uuid4().hex[:8]}",
        output_file_pattern="//render/#.png",
        status=status,
        start_frame=1,
        end_frame=1,
    )


@pytest.fixture
def project_and_asset(db):
    version, _ = SupportedBlenderVersion.objects.get_or_create(
        series="4.2",
        defaults={"resolved_version": "4.2.19", "is_default": True},
    )
    from workers.models import Project
    project = Project.objects.create(
        name="StatusProject",
        blender_version=version,
    )
    from django.core.files.base import ContentFile
    from workers.models import Asset
    asset = Asset(
        project=project,
        name="StatusAsset",
        blend_file=ContentFile(b"BLENDER" + b"\x00" * 57, name="a.blend"),
    )
    asset.save()
    return project, asset


@pytest.mark.django_db
class TestMainListener404:
    """The main URLconf must not route /api/status/public/."""

    def test_main_listener_returns_404(self):
        # Default ROOT_URLCONF = sethlans_manager.urls; the regex catch-
        # all excludes paths starting with 'api/', so a request to this
        # path falls through to a bare 404.
        resp = Client().get("/api/status/public/")
        assert resp.status_code == 404


@pytest.mark.urls("sethlans_manager.urls_loopback")
@pytest.mark.django_db
class TestLoopbackStatusPublic:
    """Exercises the loopback URLconf's only registered path."""

    def test_response_shape_has_exact_keys(self):
        resp = Client().get("/api/status/public/")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == _EXPECTED_KEYS
        assert isinstance(body["boot_id"], str)
        assert isinstance(body["version"], str)
        assert isinstance(body["setup_mode"], bool)
        assert isinstance(body["workers_online"], int)
        assert isinstance(body["jobs_queued"], int)
        assert isinstance(body["jobs_rendering"], int)

    def test_boot_id_matches_runtime_state(self):
        resp = Client().get("/api/status/public/")
        assert resp.json()["boot_id"] == runtime_state.manager_boot_id

    def test_workers_online_counts_only_idle_and_rendering(self):
        _make_worker("host-idle", "IDLE")
        _make_worker("host-rend", "RENDERING")
        _make_worker("host-off", "OFFLINE")

        resp = Client().get("/api/status/public/")
        assert resp.status_code == 200
        # Cache is empty at test start, so this is a fresh count.
        assert resp.json()["workers_online"] == 2

    def test_job_counts_by_status(self, project_and_asset):
        project, asset = project_and_asset
        _make_job(asset, "QUEUED")
        _make_job(asset, "QUEUED")
        _make_job(asset, "RENDERING")
        # DONE / ERROR must not be counted — build one and assert.
        _make_job(asset, "DONE")

        resp = Client().get("/api/status/public/")
        body = resp.json()
        assert body["jobs_queued"] == 2
        assert body["jobs_rendering"] == 1

    def test_setup_mode_true_when_sentinel_absent(self, tmp_path, mocker):
        mocker.patch(
            "workers.views.status_public._get_data_dir",
            return_value=tmp_path,
        )
        resp = Client().get("/api/status/public/")
        assert resp.json()["setup_mode"] is True

    def test_setup_mode_false_after_sentinel(self, tmp_path, mocker):
        from workers.services.sentinel import create_sentinel
        mocker.patch(
            "workers.views.status_public._get_data_dir",
            return_value=tmp_path,
        )
        create_sentinel(tmp_path, "manager", ["verified"])
        resp = Client().get("/api/status/public/")
        assert resp.json()["setup_mode"] is False


@pytest.mark.urls("sethlans_manager.urls_loopback")
@pytest.mark.django_db
class TestCountCacheBehavior:
    """The 2 s in-process cache must honor TTL and boot_id invalidation."""

    def test_cache_hides_new_worker_within_ttl(self):
        _make_worker("host-a", "IDLE")
        r1 = Client().get("/api/status/public/")
        assert r1.json()["workers_online"] == 1

        # Create another worker AFTER the count was cached.  Within the
        # 2 s TTL, the cached value should still be returned.
        _make_worker("host-b", "IDLE")
        r2 = Client().get("/api/status/public/")
        assert r2.json()["workers_online"] == 1  # cache hit

    def test_cache_expires_after_ttl(self, mocker):
        _make_worker("host-a", "IDLE")
        # First probe caches workers_online=1.
        r1 = Client().get("/api/status/public/")
        assert r1.json()["workers_online"] == 1
        _make_worker("host-b", "IDLE")

        # Advance monotonic by >2 s so the cache entry is stale.  Patch
        # the module's time.monotonic handle so we don't actually sleep.
        base = time.monotonic()
        mocker.patch.object(
            status_public_mod.time, "monotonic",
            return_value=base + 5.0,
        )
        r2 = Client().get("/api/status/public/")
        assert r2.json()["workers_online"] == 2

    def test_boot_id_change_invalidates_cache(self):
        _make_worker("host-a", "IDLE")
        # Warm the cache under the pinned boot_id.
        r1 = Client().get("/api/status/public/")
        assert r1.json()["workers_online"] == 1
        _make_worker("host-b", "IDLE")

        # Rotate boot_id — simulates a manager restart.  Next call must
        # miss the old cache entry even though TTL has not elapsed.
        runtime_state.manager_boot_id = uuid.uuid4().hex
        r2 = Client().get("/api/status/public/")
        assert r2.json()["workers_online"] == 2


class TestUvicornLauncherLoopbackBinding:
    """Socket-level loopback restriction lives in ``uvicorn_launcher.launch``.

    We cannot spin up the real dual-listener stack inside the test
    process (it calls ``asyncio.run`` and owns the event loop), so we
    assert the critical wiring by inspecting the ``uvicorn.Config`` that
    the launcher constructs for the loopback server.
    """

    def test_loopback_config_binds_to_127_0_0_1(self, mocker, tmp_path):
        import uvicorn

        from sethlans_manager import uvicorn_launcher as ul

        configs: list[uvicorn.Config] = []

        class _ServerStub:
            def __init__(self, cfg):
                configs.append(cfg)

            async def serve(self):
                return None

        mocker.patch.object(ul.uvicorn, "Server", _ServerStub)
        # Short-circuit asyncio.run so the test does not spin up loops.
        mocker.patch.object(ul.asyncio, "run", lambda *_a, **_kw: None)
        # Avoid touching Windows event-loop policy.
        mocker.patch.object(
            ul, "_install_selector_policy", lambda: None,
        )

        cert = tmp_path / "cert.pem"
        cert.write_bytes(b"x")
        key = tmp_path / "key.pem"
        key.write_bytes(b"x")

        ul.launch(
            host="0.0.0.0",
            port="8080",
            cert_path=cert,
            key_path=key,
            dev_mode=False,
            manager_dir=tmp_path,
            get_loopback_port=lambda: "8088",
        )

        assert len(configs) == 2
        main_cfg, loopback_cfg = configs
        assert main_cfg.host == "0.0.0.0"
        assert loopback_cfg.host == "127.0.0.1"
        assert loopback_cfg.port == 8088
        # The loopback listener must be plaintext — no TLS kwargs.
        assert getattr(loopback_cfg, "ssl_certfile", None) in (None, "")
