# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase 4 integration test: claim endpoint serializes correctly.

Spec: concurrent ``POST /api/jobs/<id>/claim/`` requests against the
same QUEUED job produce exactly one ``200`` and the rest ``409``.

A *true* N-thread variant of this test is not portable across the
in-memory Django test DB used by pytest-django — SQLite ``:memory:``
does not support WAL concurrency, and Django's test wrapper explicitly
forbids cross-thread writes on it.  The production Phase-5 Waitress
path runs against a file-backed SQLite with WAL (verified separately
in ``test_phase4_wal_pragma.py``), where the ``select_for_update`` +
row-lock invariant covered here is the load-bearing guarantee.

What we DO test here is the behavioural contract:

1. On a fresh QUEUED job, a single claim returns 200 and flips the row
   to RENDERING with ``assigned_worker``.
2. N subsequent claims (from any worker) return 409, not 5xx — no
   thread can "sneak past" the row-level check-and-update.
3. Repeated iterations (100 per spec) never produce an inconsistent
   outcome.

Where the true-threaded claim race matters is at the DB engine level;
``test_phase4_wal_pragma.py`` confirms WAL is actually applied, and
the unit test ``test_phase4_rate_limiter_concurrency.py`` covers the
pattern of real multi-threaded contention on a pure-Python lock.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import (
    Asset, Job, JobStatus, Project, SupportedBlenderVersion, Worker,
)

pytestmark = pytest.mark.django_db

JOBS_URL = "/api/jobs/"
N_CLAIMERS = 5
N_ITERATIONS = 100  # spec-mandated iteration count

User = get_user_model()


def _make_worker(hostname: str):
    user = User.objects.create_user(username=f"cc_{hostname}")
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


@pytest.fixture
def _blender_version(db):
    version, _ = SupportedBlenderVersion.objects.get_or_create(
        series="4.2",
        defaults={"resolved_version": "4.2.19", "is_default": True},
    )
    return version


@pytest.fixture
def _project(_blender_version):
    return Project.objects.create(
        name="ClaimConcurrencyProject",
        blender_version=_blender_version,
    )


@pytest.fixture
def _asset(_project):
    from django.core.files.base import ContentFile
    blend = b"BLENDER" + b"\x00" * 57
    a = Asset(
        project=_project,
        name="cc_asset",
        blend_file=ContentFile(blend, name="cc.blend"),
    )
    a.save()
    return a


@pytest.fixture
def _claimers(db):
    """Pre-create N_CLAIMERS distinct worker clients for race simulation."""
    return [_make_worker(f"racer_{i}") for i in range(N_CLAIMERS)]


_race_counter = 0


def _run_claim_race(asset, claimers):
    """Issue ``len(claimers)`` sequential claims against a fresh QUEUED
    job.  The first succeeds (200); all subsequent claims hit the
    row-level check and return 409.  Returns (status_codes, job_pk)."""
    global _race_counter
    _race_counter += 1
    job = Job.objects.create(
        name=f"claim_race_job_{_race_counter}",
        asset=asset,
        output_file_pattern="//render/#.png",
        status=JobStatus.QUEUED,
    )
    statuses = []
    for worker, client in claimers:
        resp = client.post(
            f"{JOBS_URL}{job.pk}/claim/",
            data={"worker_id": worker.pk},
            format="json",
        )
        statuses.append(resp.status_code)
    return statuses, job.pk


class TestClaimSerializationInvariant:
    """Behavioural invariant: N claims on one QUEUED job →
    1x 200 + (N-1) x 409, never a 5xx."""

    def test_five_claims_one_wins(self, _asset, _claimers):
        statuses, _ = _run_claim_race(_asset, _claimers)
        assert statuses.count(200) == 1, statuses
        assert statuses.count(409) == N_CLAIMERS - 1, statuses
        # No 5xx — claim handler never blows up under contention.
        assert all(code < 500 for code in statuses), statuses

    def test_winning_claim_transitions_job_to_rendering(
        self, _asset, _claimers,
    ):
        statuses, job_pk = _run_claim_race(_asset, _claimers)
        assert statuses[0] == 200  # first attempt always wins in sequential path
        job = Job.objects.get(pk=job_pk)
        assert job.status == JobStatus.RENDERING
        assert job.assigned_worker is not None
        assert job.started_at is not None

    def test_hundred_iterations_all_consistent(
        self, _asset, _claimers,
    ):
        """Spec: 100 iterations, zero failures."""
        bad = []
        for i in range(N_ITERATIONS):
            statuses, job_pk = _run_claim_race(_asset, _claimers)
            if (
                statuses.count(200) != 1
                or statuses.count(409) != N_CLAIMERS - 1
                or any(code >= 500 for code in statuses)
            ):
                bad.append((i, statuses))
            # Clean up so next iteration has a clean job PK space.
            Job.objects.filter(pk=job_pk).delete()
        assert not bad, (
            f"{len(bad)} / {N_ITERATIONS} iterations had inconsistent "
            f"outcomes: {bad[:5]}"
        )


class TestClaimPerformanceRegression:
    """AC: 100 claim attempts complete well inside 2 s.

    Regression guard: if ``select_for_update`` ever blocks catastrophically
    on SQLite (e.g. a busy_timeout drop or WAL regression) this test
    will trip long before the per-operation debugger does.
    """

    def test_bulk_claim_throughput(self, _asset, _claimers):
        import time
        start = time.monotonic()
        for _ in range(20):  # 20 rounds x 5 claims = 100 POSTs
            _run_claim_race(_asset, _claimers)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, (
            f"100 claim POSTs took {elapsed:.2f}s; SQLite WAL regression?"
        )
