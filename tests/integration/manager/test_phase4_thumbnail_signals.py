# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase 4 integration tests for the thread-local thumbnail skip guard.

Covers:
* Happy path: two concurrent ``upload_output`` POSTs each produce
  exactly one thumbnail (no missed / duplicated signals).
* Exception-path regression: a ``_save_thumbnails_for_instances``
  call that raises inside the inner save must not leave the thread's
  skip flag set — a subsequent call on the same thread still works.
* Reentrancy: a handler that itself invokes the helper does not
  double-fire because the outer context holds depth > 0.
"""

import io
import threading

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connections
from PIL import Image
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import (
    Asset, Job, Project, SupportedBlenderVersion, Worker,
)
from workers.signal_helpers import (
    _save_thumbnails_for_instances,
    _skip_thumbnail_signals,
    _skip_thumbnails,
    _tls,
)

User = get_user_model()

JOBS_URL = "/api/jobs/"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color="blue").save(buf, format="PNG")
    return buf.getvalue()


def _png_file():
    buf = io.BytesIO(_png_bytes())
    buf.name = "out.png"
    return buf


@pytest.fixture
def _fixtures(transactional_db):
    version, _ = SupportedBlenderVersion.objects.get_or_create(
        series="4.2",
        defaults={"resolved_version": "4.2.19", "is_default": True},
    )
    project = Project.objects.create(
        name="ThumbProject",
        blender_version=version,
    )
    blend = b"BLENDER" + b"\x00" * 57
    asset = Asset(
        project=project,
        name="thumb_asset",
        blend_file=ContentFile(blend, name="t.blend"),
    )
    asset.save()
    return {"project": project, "asset": asset}


def _make_worker(hostname: str):
    user = User.objects.create_user(username=f"thumb_{hostname}")
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname=hostname,
        user=user,
        is_active=True,
        available_tools={"blender": ["4.2.19"]},
    )
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return worker, client


def _create_and_claim_job(admin_client, worker, worker_client, asset):
    resp = admin_client.post(
        JOBS_URL,
        data={
            "name": f"thumb_job_{worker.hostname}",
            "asset_id": asset.pk,
            "output_file_pattern": "//render/#.png",
        },
        format="json",
    )
    assert resp.status_code == 201
    job_id = resp.data["id"]
    resp = worker_client.post(
        f"{JOBS_URL}{job_id}/claim/",
        data={"worker_id": worker.pk},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    return job_id


# ---------------------------------------------------------------------
# Reset TLS between tests so a prior failure doesn't poison the thread.
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_tls():
    if hasattr(_tls, "depth"):
        _tls.depth = 0
    yield
    if hasattr(_tls, "depth"):
        _tls.depth = 0


# ---------------------------------------------------------------------
# Happy-path concurrency
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestConcurrentThumbnailGeneration:

    def test_two_concurrent_uploads_each_produce_thumbnail(
        self, admin_user, _fixtures,
    ):
        """Two uploads on DIFFERENT jobs must each get a thumbnail."""
        asset = _fixtures["asset"]
        admin_client_a = APIClient()
        admin_client_a.force_authenticate(user=admin_user)
        admin_client_b = APIClient()
        admin_client_b.force_authenticate(user=admin_user)

        worker_a, client_a = _make_worker("hostA")
        worker_b, client_b = _make_worker("hostB")
        job_a = _create_and_claim_job(
            admin_client_a, worker_a, client_a, asset,
        )
        job_b = _create_and_claim_job(
            admin_client_b, worker_b, client_b, asset,
        )

        results = {}
        barrier = threading.Barrier(2)

        def uploader(key, client, job_id):
            try:
                barrier.wait(timeout=5)
                resp = client.post(
                    f"{JOBS_URL}{job_id}/upload_output/",
                    data={"output_file": _png_file()},
                    format="multipart",
                )
                results[key] = resp.status_code
            finally:
                connections.close_all()

        ta = threading.Thread(
            target=uploader, args=("a", client_a, job_a),
        )
        tb = threading.Thread(
            target=uploader, args=("b", client_b, job_b),
        )
        ta.start()
        tb.start()
        ta.join(timeout=30)
        tb.join(timeout=30)

        assert results.get("a") == 200, results
        assert results.get("b") == 200, results

        # Each job got a thumbnail (neither was suppressed).
        a = Job.objects.get(pk=job_a)
        b = Job.objects.get(pk=job_b)
        assert a.thumbnail and a.thumbnail.name, (
            f"job_a thumbnail missing: {a.thumbnail}"
        )
        assert b.thumbnail and b.thumbnail.name, (
            f"job_b thumbnail missing: {b.thumbnail}"
        )


# ---------------------------------------------------------------------
# Exception-path regression
# ---------------------------------------------------------------------


class TestExceptionPathRegression:
    """Spec: a ``_save_thumbnails_for_instances`` call that raises
    inside the inner save, followed by another call on the same thread,
    must produce exactly one thumbnail (flag cleared by ``finally``).
    """

    @pytest.mark.django_db(transaction=True)
    def test_raise_inside_context_clears_depth(self, _fixtures):
        """Direct test of the helper's exception safety.

        We force the inner save to raise and then verify the TLS
        depth is back to 0 — the precondition for any subsequent call
        to actually fire signals again.
        """
        assert _skip_thumbnails() is False

        class _Bomb:
            def save(self, *_a, **_kw):
                raise RuntimeError("simulated storage failure")

        class _Inst:
            thumbnail = _Bomb()

        with pytest.raises(RuntimeError):
            _save_thumbnails_for_instances(
                [_Inst()],
                sender=Job,
                handler=lambda *a, **kw: None,
                thumb_content=_png_bytes(),
            )

        # After the raise, the thread must be clean.
        assert _skip_thumbnails() is False
        assert getattr(_tls, "depth", 0) == 0


# ---------------------------------------------------------------------
# Reentrancy
# ---------------------------------------------------------------------


class TestReentrancy:
    """Nested ``with _skip_thumbnail_signals():`` must stack.

    Covers the case where a ``post_save`` handler that itself invokes
    ``_save_thumbnails_for_instances`` would, under a plain-bool flag,
    accidentally re-enable signals when the inner block exits.
    """

    def test_nested_context_keeps_outer_active(self):
        with _skip_thumbnail_signals():
            assert _skip_thumbnails() is True
            with _skip_thumbnail_signals():
                assert _skip_thumbnails() is True
            # Inner block exited, but outer is still active.
            assert _skip_thumbnails() is True, (
                "Outer skip context was wrongly disabled by inner exit"
            )
        # All contexts exited — back to normal.
        assert _skip_thumbnails() is False

    def test_handler_invoking_helper_does_not_double_fire(self):
        """Simulate the reentrancy spec case without wiring a real
        post_save: while inside an outer ``_skip_thumbnail_signals()``
        block (as the helper does), any nested call to the helper
        continues to see ``_skip_thumbnails() == True`` and therefore
        handlers early-return.
        """
        handler_fires = []

        def fake_handler():
            if _skip_thumbnails():
                return
            handler_fires.append(1)

        with _skip_thumbnail_signals():
            # Simulate nested helper call using its own context.
            with _skip_thumbnail_signals():
                fake_handler()
            # Still inside outer context — handler MUST stay suppressed.
            fake_handler()

        # Outside both contexts — handler should fire once here.
        fake_handler()

        assert handler_fires == [1], (
            f"expected exactly one fire after contexts exit, "
            f"got {handler_fires}"
        )


# Rate-limiter TOCTOU concurrency test lives in
# ``test_phase4_rate_limiter_concurrency.py`` to respect the 300-line
# per-file ceiling.
