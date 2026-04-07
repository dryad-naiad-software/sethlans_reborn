# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for ``JobViewSet`` device_prefs filter (FR-32).

The worker capacity gate replaces the legacy ``gpu_available`` poll
parameter with a ``device_prefs`` CSV query parameter that whitelists
against ``workers.constants.RenderDevice.values``. Invalid values and
empty-parsed lists must produce HTTP 400; an omitted parameter is a
pure no-op at the queryset level.

These tests seed the manager database with at least one job of each
device preference and exercise every branch required by FR-32.
"""

import pytest

from workers.models import Job

JOBS_URL = '/api/jobs/'


@pytest.fixture
def jobs_with_each_device_pref(admin_client, asset):
    """Create three jobs -- one CPU, one GPU, one ANY."""
    created = {}
    for pref in ('CPU', 'GPU', 'ANY'):
        resp = admin_client.post(
            JOBS_URL,
            data={
                'name': f'Job{pref}',
                'asset_id': asset.pk,
                'output_file_pattern': f'//render/{pref}/#.png',
                'render_device': pref,
            },
            format='json',
        )
        assert resp.status_code == 201, resp.content
        created[pref] = resp.data['id']
    return created


def _list_names(resp):
    """Return the list of job names from a DRF list response.

    Handles both the paginated (``{'results': [...]}``) and
    non-paginated (``[...]``) shapes so the test doesn't have to know
    whether pagination is enabled on the viewset.
    """
    data = resp.data
    if isinstance(data, dict) and 'results' in data:
        data = data['results']
    return sorted(item['name'] for item in data)


def _device_prefs_of(resp):
    """Return the set of render_device values from a job list response."""
    data = resp.data
    if isinstance(data, dict) and 'results' in data:
        data = data['results']
    return {item['render_device'] for item in data}


@pytest.mark.django_db
class TestDevicePrefsFilter:
    """FR-32: ``device_prefs`` CSV filter on ``GET /api/jobs/``."""

    def test_omitted_param_is_a_no_op(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """No device_prefs param -> unchanged behaviour (all jobs)."""
        resp = admin_client.get(JOBS_URL)
        assert resp.status_code == 200
        names = _list_names(resp)
        assert names == sorted(['JobCPU', 'JobGPU', 'JobANY'])

    def test_gpu_only_excludes_any(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """device_prefs=GPU returns only GPU jobs (explicitly NOT ANY)."""
        resp = admin_client.get(JOBS_URL, {'device_prefs': 'GPU'})
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'GPU'}
        names = _list_names(resp)
        assert names == ['JobGPU']

    def test_cpu_only_excludes_any(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """device_prefs=CPU returns only CPU jobs (explicitly NOT ANY)."""
        resp = admin_client.get(JOBS_URL, {'device_prefs': 'CPU'})
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'CPU'}

    def test_any_only_excludes_cpu_and_gpu(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """device_prefs=ANY returns only ANY jobs."""
        resp = admin_client.get(JOBS_URL, {'device_prefs': 'ANY'})
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'ANY'}

    def test_gpu_and_any(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """device_prefs=GPU,ANY returns GPU and ANY jobs."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': 'GPU,ANY'},
        )
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'GPU', 'ANY'}

    def test_cpu_and_any(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """device_prefs=CPU,ANY returns CPU and ANY jobs."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': 'CPU,ANY'},
        )
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'CPU', 'ANY'}

    def test_gpu_cpu_and_any(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """device_prefs=GPU,CPU,ANY returns all three."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': 'GPU,CPU,ANY'},
        )
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'GPU', 'CPU', 'ANY'}

    def test_case_insensitive_values(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """Lowercase values are normalised before validation."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': 'gpu,any'},
        )
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'GPU', 'ANY'}

    def test_whitespace_tolerance(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """Surrounding whitespace on individual values is stripped."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': ' GPU , ANY '},
        )
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'GPU', 'ANY'}


@pytest.mark.django_db
class TestDevicePrefsFilterValidation:
    """FR-9b / FR-32: invalid and empty values must return HTTP 400."""

    def test_invalid_value_returns_400(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """Unknown device pref -> HTTP 400 naming the offending value."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': 'INVALID'},
        )
        assert resp.status_code == 400
        # ValidationError serialises to a list on DRF; coerce to string.
        body = str(resp.content).upper()
        assert 'INVALID' in body

    def test_mixed_valid_and_invalid_returns_400(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """A single invalid value in a mixed CSV is enough to 400."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': 'GPU,BANANA,ANY'},
        )
        assert resp.status_code == 400
        body = str(resp.content).upper()
        assert 'BANANA' in body

    def test_empty_string_returns_400(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """device_prefs='' (present but empty) -> HTTP 400."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': ''},
        )
        assert resp.status_code == 400

    def test_only_whitespace_returns_400(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """device_prefs of just whitespace and commas parses to empty."""
        resp = admin_client.get(
            JOBS_URL, {'device_prefs': ' , , '},
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestLegacyGpuAvailableRemoved:
    """FR-9a: legacy ``gpu_available`` query parameter is deleted.

    The previous release translated ``gpu_available=true`` into
    ``render_device in (GPU, ANY)``. In the capacity-gate rewrite the
    parameter is no longer parsed; if the worker still sends it, the
    viewset must behave as though it were absent (NOT translate it into
    a queryset filter). Nothing in the manager code should 400 on it
    either -- it's simply ignored.
    """

    def test_gpu_available_true_is_ignored(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """gpu_available=true does NOT filter CPU jobs out."""
        resp = admin_client.get(
            JOBS_URL, {'gpu_available': 'true'},
        )
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        # The CPU job must still be in the response -- legacy
        # translation is gone.
        assert 'CPU' in prefs
        assert prefs == {'CPU', 'GPU', 'ANY'}

    def test_gpu_available_false_is_ignored(
        self, admin_client, jobs_with_each_device_pref,
    ):
        """gpu_available=false does NOT filter GPU jobs out."""
        resp = admin_client.get(
            JOBS_URL, {'gpu_available': 'false'},
        )
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'CPU', 'GPU', 'ANY'}


@pytest.mark.django_db
class TestDevicePrefsFilterWithWorkerPoll:
    """Worker-shaped poll (status + assigned_worker__isnull) + device_prefs."""

    def test_worker_poll_with_device_prefs_filters_correctly(
        self, worker_with_token, admin_client, asset,
        jobs_with_each_device_pref,
    ):
        """device_prefs combined with the worker-poll trigger params."""
        _, worker_client = worker_with_token

        # All jobs created by the admin fixture are QUEUED + unassigned,
        # which matches the worker-poll filter shape.
        resp = worker_client.get(
            JOBS_URL,
            {
                'status': 'QUEUED',
                'assigned_worker__isnull': 'true',
                'device_prefs': 'GPU,ANY',
            },
        )
        assert resp.status_code == 200
        prefs = _device_prefs_of(resp)
        assert prefs == {'GPU', 'ANY'}

    def test_worker_poll_without_device_prefs_returns_all(
        self, worker_with_token, jobs_with_each_device_pref,
    ):
        """Worker poll with no device_prefs -> unchanged behaviour."""
        _, worker_client = worker_with_token

        resp = worker_client.get(
            JOBS_URL,
            {
                'status': 'QUEUED',
                'assigned_worker__isnull': 'true',
            },
        )
        assert resp.status_code == 200
        # All three jobs are visible to the worker.
        assert Job.objects.count() == 3
        prefs = _device_prefs_of(resp)
        assert prefs == {'CPU', 'GPU', 'ANY'}
