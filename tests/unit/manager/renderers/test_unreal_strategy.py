# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the Unreal strategy's flags and the pure routing helpers
(Unreal foundation FR-MR-8, FR-RT-5, FR-RT-14, FR-T-4, AC-44c).
"""

from types import SimpleNamespace

import pytest

from workers.models.workers import Worker, reported_unreal_builds, reports_share_reachable
from workers.renderers import get_strategy
from workers.renderers.unreal import UNREAL_WORKER_PATCH_FIELDS
from workers.services.worker_job_writes import WORKER_PATCH_FIELDS

BUILD = '5.8.4-58902052'
SHARE = r'\\alpha.lan\share\unreal'


def _caps(**extra):
    caps = {
        'schema': 1, 'enabled': True, 'disabled_reason': '',
        'installs': [
            {'build': BUILD, 'supported': True, 'source': 'launcher'},
            {'build': '5.7.4-51494982', 'supported': False, 'source': 'launcher'},
        ],
        'share': SHARE, 'share_reachable': True,
    }
    caps.update(extra)
    return caps


@pytest.fixture
def unreal():
    return get_strategy('UNREAL')


def test_flags(unreal):
    assert not unreal.supports('tiled_job')
    assert not unreal.supports('animation')
    assert unreal.idempotent_reclaim() is True
    assert unreal.requeue_on_worker_restart() is True
    assert unreal.supports_release() is True


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


class TestReportedUnrealBuilds:

    def test_supported_builds_only(self):
        assert reported_unreal_builds(_caps()) == {BUILD}

    @pytest.mark.parametrize('caps', [
        {}, None, [], _caps(enabled=False), _caps(enabled='true'), _caps(installs='x'),
        _caps(installs=[{'build': BUILD, 'supported': 'yes'}]),
    ])
    def test_nothing_unless_enabled_and_supported(self, caps):
        assert reported_unreal_builds(caps) == frozenset()

    def test_has_unreal_build(self):
        worker = Worker(hostname='w', unreal_capabilities=_caps())
        assert worker.has_unreal_build(BUILD)
        assert not worker.has_unreal_build('5.7.4-51494982')


class TestReportsShareReachable:

    def test_matching_reachable_share(self):
        assert reports_share_reachable(_caps(), SHARE)

    @pytest.mark.parametrize('reported', [r'\\ALPHA.LAN\Share\Unreal', SHARE + '\\'])
    def test_case_and_trailing_separator_do_not_matter(self, reported):
        assert reports_share_reachable(_caps(share=reported), SHARE)

    @pytest.mark.parametrize('caps', [
        _caps(enabled=False), _caps(share_reachable=False), _caps(share_reachable='true'),
        _caps(share=''), _caps(share=r'\\alpha\share\unreal'), {k: v for k, v in _caps().items() if k != 'share'},
        None,
    ])
    def test_refusals(self, caps):
        assert not reports_share_reachable(caps, SHARE)

    def test_unset_manager_share(self):
        assert not reports_share_reachable(_caps(), '')
