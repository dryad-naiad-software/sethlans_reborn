# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract test for renderer-aware projects (Unreal
foundation spec FR-T-12, AC-25).

The hand-written Angular interfaces in ``core/services/`` are the real
frontend contract (``api-types.ts`` is not imported by any code). This
test checks them against real API responses for a Blender row and an
Unreal row, so the fixtures in ``*.component.unreal.spec.ts`` stay honest:

* every field of the TS ``Project`` interface is present in both
  ``GET /api/projects/`` objects and in ``Asset.project_details``;
* an Unreal row returns ``null`` exactly where the TS type admits
  ``| null`` (``blender_version``, ``blender_version_details``,
  ``Asset.blend_file``, ``Job.effective_blender_version``);
* ``renderer`` is one of the TS union literals.

Chunk 1's ``UnrealStrategy`` stub rejects Unreal creation through the
API, so the Unreal rows are created through the ORM; the Blender project
goes through the API.
"""

from __future__ import annotations

import re

import pytest

from workers.constants import Renderer
from workers.models import Asset, Job, Project

from ._ffmpeg_contract_helpers import FRONTEND_ROOT, is_optional_field, parse_ts_interface

SERVICES = FRONTEND_ROOT / "core" / "services"
PROJECT_TS = SERVICES / "project.service.ts"
ASSET_TS = SERVICES / "asset.service.ts"
JOB_TS = SERVICES / "job.service.ts"

BUILD = "5.8.4-58902052"
NULLABLE_FOR_UNREAL = ("blender_version", "blender_version_details")


def _interface(path, name: str) -> dict[str, str]:
    return parse_ts_interface(path.read_text(encoding="utf-8"), name)


def _union_literals(ts_type: str) -> set[str]:
    """``"'BLENDER' | 'UNREAL'"`` -> ``{'BLENDER', 'UNREAL'}``."""
    return set(re.findall(r"'([^']*)'", ts_type))


def _assert_project_shape(row: dict, ts: dict[str, str]) -> None:
    missing = set(ts) - set(row)
    assert not missing, f"TS Project fields missing from the API row: {sorted(missing)}"
    assert row["renderer"] in _union_literals(ts["renderer"])


def _assert_unreal_project(row: dict, ts: dict[str, str]) -> None:
    _assert_project_shape(row, ts)
    assert row["renderer"] == "UNREAL"
    assert row["engine_build"] == BUILD
    for field in NULLABLE_FOR_UNREAL:
        assert row[field] is None, field
        assert "| null" in ts[field], f"TS Project.{field} must admit null"


def _assert_blender_project(row: dict, ts: dict[str, str], version) -> None:
    _assert_project_shape(row, ts)
    assert row["renderer"] == "BLENDER"
    assert row["engine_build"] == ""
    assert row["blender_version"] == version.pk
    assert row["blender_version_details"]["series"] == version.series
    assert row["blender_version_details"]["resolved_version"] == version.resolved_version


@pytest.fixture
def project_ts() -> dict[str, str]:
    return _interface(PROJECT_TS, "Project")


@pytest.fixture
def unreal_project(db):
    return Project.objects.create(
        name="ContractUnreal", renderer="UNREAL", engine_build=BUILD, blender_version=None,
    )


@pytest.fixture
def unreal_asset(unreal_project):
    return Asset.objects.create(
        name="ContractUnrealAsset", project=unreal_project, source_type="shared_project",
        archive_path="incoming/scene.zip", uproject_path="Scene/Scene.uproject",
        archive_sha256="c" * 64,
    )


@pytest.mark.django_db
class TestProjectInterface:
    """``Project`` (project.service.ts) <-> ``GET /api/projects/``."""

    def test_renderer_fields_are_required_in_ts(self):
        source = PROJECT_TS.read_text(encoding="utf-8")
        for field in ("renderer", "engine_build"):
            assert not is_optional_field(source, "Project", field), field

    def test_ts_renderer_union_matches_the_model_choices(self, project_ts):
        assert _union_literals(project_ts["renderer"]) == set(Renderer.values)

    def test_list_rows_match_ts_for_both_renderers(
        self, admin_client, default_version, unreal_project, project_ts,
    ):
        created = admin_client.post(
            "/api/projects/", {"name": "ContractBlender"}, format="json",
        )
        assert created.status_code == 201, created.data

        resp = admin_client.get("/api/projects/")
        assert resp.status_code == 200
        rows = {row["name"]: row for row in resp.json()}

        _assert_blender_project(rows["ContractBlender"], project_ts, default_version)
        _assert_unreal_project(rows["ContractUnreal"], project_ts)

    def test_detail_row_matches_ts_for_unreal(self, admin_client, unreal_project, project_ts):
        resp = admin_client.get(f"/api/projects/{unreal_project.pk}/")
        assert resp.status_code == 200
        _assert_unreal_project(resp.json(), project_ts)


@pytest.mark.django_db
class TestAssetInterface:
    """``Asset.project_details`` / ``Asset.blend_file`` <-> ``GET /api/assets/``."""

    def test_project_details_match_ts_for_both_renderers(
        self, admin_client, asset, unreal_asset, default_version, project_ts,
    ):
        resp = admin_client.get("/api/assets/")
        assert resp.status_code == 200
        rows = {row["id"]: row for row in resp.json()}

        _assert_blender_project(rows[asset.pk]["project_details"], project_ts, default_version)
        _assert_unreal_project(rows[unreal_asset.pk]["project_details"], project_ts)

    def test_shared_project_blend_file_is_null(self, admin_client, asset, unreal_asset):
        asset_ts = _interface(ASSET_TS, "Asset")
        resp = admin_client.get("/api/assets/")
        rows = {row["id"]: row for row in resp.json()}

        assert rows[unreal_asset.pk]["blend_file"] is None
        assert "| null" in asset_ts["blend_file"]
        assert isinstance(rows[asset.pk]["blend_file"], str)


@pytest.mark.django_db
class TestJobInterface:
    """``Job.effective_blender_version`` <-> ``GET /api/jobs/`` for an Unreal job."""

    def test_unreal_job_effective_blender_version_is_null(self, admin_client, unreal_asset):
        job = Job.objects.create(
            name="ContractUnrealJob", asset=unreal_asset, output_file_pattern="frame_####.png",
        )
        job_ts = _interface(JOB_TS, "Job")

        resp = admin_client.get(f"/api/jobs/{job.pk}/")
        assert resp.status_code == 200, resp.content

        assert resp.json()["effective_blender_version"] is None
        assert "| null" in job_ts["effective_blender_version"]
