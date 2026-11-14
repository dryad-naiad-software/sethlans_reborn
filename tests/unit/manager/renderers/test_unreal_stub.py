# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the chunk-1 Unreal strategy refuses every Unreal request.

Chunk 2 of the Unreal foundation spec replaces this stub with the real
rules; until then nothing Unreal can be created, polled or claimed.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db.models import Q
from django.http import QueryDict
from rest_framework.exceptions import ValidationError

from workers.renderers import get_strategy
from workers.renderers.unreal import NOT_SUPPORTED_MESSAGE, UNREAL_WORKER_PATCH_FIELDS
from workers.services.worker_job_writes import WORKER_PATCH_FIELDS


@pytest.fixture
def unreal():
    return get_strategy('UNREAL')


@pytest.mark.parametrize('call, field', [
    (lambda s: s.validate_project({'renderer': 'UNREAL'}, None), 'renderer'),
    (lambda s: s.prepare_project_create({}), 'renderer'),
    (lambda s: s.validate_asset({}, None), 'source_type'),
    (lambda s: s.validate_job({}, MagicMock(), None), 'asset_id'),
    (lambda s: s.prepare_job_create({}, MagicMock()), 'asset_id'),
])
def test_request_path_methods_raise_drf_400(unreal, call, field):
    with pytest.raises(ValidationError) as exc:
        call(unreal)
    assert exc.value.detail == {field: [NOT_SUPPORTED_MESSAGE]}


def test_poll_q_matches_nothing(unreal):
    q = unreal.poll_q(QueryDict('renderers=UNREAL&unreal_builds=5.8.4-58902052'))
    assert q == Q(pk__in=[])


def test_claim_is_always_rejected(unreal):
    assert unreal.claim_rejection(MagicMock(), MagicMock()) == NOT_SUPPORTED_MESSAGE


def test_conservative_flags(unreal):
    assert not unreal.supports('tiled_job')
    assert not unreal.supports('animation')
    assert unreal.idempotent_reclaim() is False
    assert unreal.requeue_on_worker_restart() is False
    assert unreal.supports_release() is False
    assert unreal.routing_note(MagicMock(), {}) is None


def test_unreal_allowlist_is_a_subset_of_issue_217_allowlist():
    """FR-RT-14's set layers on #217: it may never widen a worker PATCH."""
    assert UNREAL_WORKER_PATCH_FIELDS <= WORKER_PATCH_FIELDS


def test_worker_patch_allowlist(unreal):
    allowed = unreal.worker_patch_allowed_fields()
    assert allowed is UNREAL_WORKER_PATCH_FIELDS
    assert allowed == {'status', 'last_output', 'error_message', 'completed_at', 'render_time_seconds'}
    assert 'started_at' not in allowed


def test_describe_asset_names_archive_and_uproject(unreal):
    asset = SimpleNamespace(archive_path='incoming/A.zip', uproject_path='A/A.uproject')
    assert unreal.describe_asset(asset) == 'Archive: incoming/A.zip (A/A.uproject)'
