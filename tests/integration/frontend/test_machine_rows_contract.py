# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract test for the per-machine memory test machine
lists (Unreal per-machine memory test FR-MT-U-1, chunk 8).

Real API answers are checked against the hand-written TS shapes with
``_ts_shape_checker`` (which follows the ``import`` of
``unreal-measurement.model.ts``):

* ``GET /api/jobs/{id}/`` of an Unreal render job: ``Job.unreal_machines``
  (``job.service.ts``, ``MachineRow[] | null``);
* ``GET /api/projects/{id}/``: ``ProjectDetail`` (``project.service.ts``,
  ``extends Project``) and its ``unreal_measurement``
  (``UnrealMeasurementSummary`` with
  ``keys: UnrealMeasurementKey[] | null``, each key with its ``machines``).

The key's machines cover every state the TS ``MachineState`` names, a
result with RAM figures (warm-up run and not run) and one without, so
the nullable result fields of ``MachineRow`` are seen both null and set.  The lists
are non-empty, so a null or empty answer cannot pass by being skipped.
"""

from __future__ import annotations

import pytest

from workers.renderers.unreal_machine import PAIR_STATES

from tests.integration.manager.machine_rows_helpers import PROJECTS, key_args, keyed_job, machine
from tests.integration.manager.machine_test_helpers import KIND_AMD, pair_record
from tests.integration.manager.unreal_helpers import unreal_asset
from tests.integration.manager.unreal_pilot_helpers import JOBS, PILOT_RAM, fits_record

from ._ffmpeg_contract_helpers import FRONTEND_ROOT
from ._ts_shape_checker import TsShapes

SERVICES = FRONTEND_ROOT / "core" / "services"
RAM_NO_WARMUP = {'prep': {'ws_mb': 4000, 'commit_mb': 8000}, 'warmup': None, 'render': {'ws_mb': 6000, 'commit_mb': 19000}}

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module")
def job_ts() -> TsShapes:
    return TsShapes.from_file(SERVICES / "job.service.ts")


@pytest.fixture(scope="module")
def project_ts() -> TsShapes:
    return TsShapes.from_file(SERVICES / "project.service.ts")


@pytest.fixture
def job(settings, tmp_path):
    """A QUEUED Unreal render job whose key has a row in every machine state."""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    job = keyed_job(unreal_asset(), name="ContractRows")
    asset, target = key_args(job)
    fits = machine("host-fits", KIND_AMD)
    fits_record(asset, target, fits, kind=KIND_AMD, ram=PILOT_RAM, driver_version="32.0.1")
    fits_record(asset, target, machine("host-fits-cold"), ram=RAM_NO_WARMUP)
    pair_record(asset, target, machine("host-dnf"), verdict="does_not_fit", lower_bound_mb=30000,
                driver_version="99.0.1")
    pair_record(asset, target, machine("host-failed"), pilot="ERROR", error="boom")
    pair_record(asset, target, machine("host-testing"), pilot="QUEUED")
    pair_record(asset, target, machine("host-stale"), verdict="fits", stale="driver_changed", driver_version="1.0")
    machine("host-untried")
    machine("host-offline", online=False)
    return job


def _get(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.content
    return resp.json()


def _assert_rows(ts: TsShapes, rows, path: str) -> None:
    """A non-empty ``MachineRow[]`` covering every state, each row matching field by field."""
    assert isinstance(rows, list) and rows, rows
    assert ts.errors(rows, "MachineRow[]", path) == []
    assert {row["state"] for row in rows} == set(PAIR_STATES)
    warmups = [row["ram"]["warmup"] for row in rows if row["ram"]]
    assert None in warmups and any(isinstance(w, dict) for w in warmups), warmups
    assert any(row["ram"] is None for row in rows)
    assert {row["online"] for row in rows} == {True, False}
    for field in ("peak_mb", "lower_bound_mb", "driver_version", "reason"):
        values = [row[field] for row in rows]
        assert None in values and any(v is not None for v in values), (field, values)


class TestMachineStateUnion:

    def test_ts_union_is_the_pair_states(self, job_ts):
        union = {m.strip("'") for m in job_ts.aliases["MachineState"].split(" | ")}
        assert union == set(PAIR_STATES)


class TestJobDetail:
    """``Job.unreal_machines`` <-> admin ``GET /api/jobs/{id}/`` of an Unreal render job."""

    def test_ts_declares_the_rows(self, job_ts):
        assert job_ts.fields("Job")["unreal_machines"] == ("MachineRow[] | null", True)

    def test_render_job_rows_match_machine_row(self, admin_client, job_ts, job):
        detail = _get(admin_client, f"{JOBS}{job.pk}/")
        assert job_ts.errors(detail["unreal_machines"], job_ts.fields("Job")["unreal_machines"][0]) == []
        _assert_rows(job_ts, detail["unreal_machines"], "$.unreal_machines")

    def test_list_rows_are_null_as_ts_admits(self, admin_client, job_ts, job):
        page = _get(admin_client, JOBS)
        rows = page["results"] if isinstance(page, dict) else page
        (row,) = [r for r in rows if r["id"] == job.pk]
        assert row.get("unreal_machines") is None


class TestProjectDetail:
    """``ProjectDetail.unreal_measurement`` <-> admin ``GET /api/projects/{id}/``."""

    def test_ts_declares_the_summary(self, project_ts):
        assert project_ts.fields("ProjectDetail")["unreal_measurement"] == ("UnrealMeasurementSummary | null", False)
        assert project_ts.fields("UnrealMeasurementSummary")["keys"] == ("UnrealMeasurementKey[] | null", False)
        assert project_ts.fields("UnrealMeasurementKey")["machines"] == ("MachineRow[]", False)

    def test_summary_and_keys_match_ts(self, admin_client, project_ts, job):
        detail = _get(admin_client, f"{PROJECTS}{job.asset.project_id}/")
        assert project_ts.object_errors(detail, "ProjectDetail") == []
        summary = detail["unreal_measurement"]
        (key,) = summary["keys"]
        assert (key["asset_id"], key["target_sha256"]) == (job.asset_id, job.unreal_target_sha256)
        _assert_rows(project_ts, key["machines"], "$.unreal_measurement.keys[0].machines")

    def test_key_rows_are_the_job_detail_rows(self, admin_client, job):
        summary = _get(admin_client, f"{PROJECTS}{job.asset.project_id}/")["unreal_measurement"]
        assert summary["keys"][0]["machines"] == _get(admin_client, f"{JOBS}{job.pk}/")["unreal_machines"]

    def test_project_list_keys_are_null_as_ts_admits(self, admin_client, project_ts, job):
        page = _get(admin_client, PROJECTS)
        rows = page["results"] if isinstance(page, dict) else page
        (row,) = [r for r in rows if r["id"] == str(job.asset.project_id)]
        assert project_ts.object_errors(row, "ProjectDetail") == []
        assert row["unreal_measurement"]["keys"] is None
