# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures for manager unit tests.

All fixtures produce plain objects or mocks — no Django DB access.
"""

import uuid
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_project_id():
    """Return a deterministic UUID for reproducible path tests."""
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def fake_asset_id():
    return uuid.UUID("aabbccdd-aabb-ccdd-aabb-ccddeeff0011")


@pytest.fixture
def mock_project(fake_project_id):
    """Mock Project with an id attribute."""
    project = MagicMock()
    project.id = fake_project_id
    project.name = "Test Project"
    project.blender_version = MagicMock()
    project.blender_version.series = "4.2"
    project.blender_version.resolved_version = "4.2.19"
    return project


@pytest.fixture
def mock_asset(mock_project, fake_asset_id):
    """Mock Asset linked to mock_project."""
    asset = MagicMock()
    asset.id = fake_asset_id
    asset.name = "Test Asset"
    asset.project = mock_project
    asset.project_id = mock_project.id
    asset.blend_file = MagicMock()
    asset.blend_file.name = "assets/12345678/abcd1234.blend"
    return asset


@pytest.fixture
def mock_job(mock_asset):
    """Mock Job linked to mock_asset, no animation or tiled_job."""
    job = MagicMock()
    job.id = 42
    job.pk = 42
    job.name = "Test Job 001"
    job.asset = mock_asset
    job.animation = None
    job.tiled_job = None
    job.output_file = MagicMock()
    job.output_file.name = "outputs/test.png"
    return job


@pytest.fixture
def mock_animation(mock_project, mock_asset):
    """Mock Animation linked to mock_project/asset."""
    anim = MagicMock()
    anim.id = 99
    anim.name = "Walk Cycle"
    anim.project = mock_project
    anim.project_id = mock_project.id
    anim.asset = mock_asset
    return anim


@pytest.fixture
def mock_animation_frame(mock_animation):
    """Mock AnimationFrame linked to mock_animation."""
    frame = MagicMock()
    frame.animation = mock_animation
    frame.frame_number = 5
    return frame


@pytest.fixture
def mock_tiled_job(mock_project, mock_asset):
    """Mock TiledJob linked to mock_project/asset."""
    tj = MagicMock()
    tj.id = uuid.UUID("99999999-0000-0000-0000-000000000001")
    tj.pk = tj.id
    tj.name = "Tiled Render"
    tj.project = mock_project
    tj.project_id = mock_project.id
    tj.asset = mock_asset
    return tj


@pytest.fixture
def mock_request():
    """Create a mock DRF request with configurable auth."""
    request = MagicMock()
    request.user = MagicMock()
    request.auth = None
    request.method = "GET"
    return request


@pytest.fixture
def mock_view():
    """Minimal mock DRF view."""
    return MagicMock()


@pytest.fixture
def mock_file_field():
    """A mock Django FieldFile with an open() context manager."""
    field = MagicMock()
    field.name = "outputs/render_001.png"
    return field
