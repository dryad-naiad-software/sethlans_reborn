# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the strict ``pilot-result/`` body's per-machine fields
(Unreal per-machine memory test FR-MT-V-3, D3, D6).

Covers: ``card_kind``, ``driver_version``, ``verdict``, ``shared_peak_mb``
and ``lower_bound_mb`` are required (a missing one is 400, an unknown key
too); each field's bounds; the ``driver_version`` pattern (40 characters
accepted, 41, a bad character or injected text refused; UE's literal
``Unknown`` and ``null`` stored as ''); ``lower_bound_mb`` equals
``peak_mb`` for ``does_not_fit`` and is null for ``fits``; the response
is ``{"status", "peak_mb"}`` without ``gate_need_mb``.  Every refusal
leaves the pilot RENDERING and its record unmeasured.
"""

import pytest

from shared.unreal.vram_gate import MAX_PEAK_MB

from .machine_verdict_helpers import assert_untouched, dnf_body
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import claimed_pilot, pilot_body, post_result, record_of

NEW_FIELDS = ('card_kind', 'driver_version', 'verdict', 'shared_peak_mb', 'lower_bound_mb')


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def pilot(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, asset, worker, client), client


def _refused(pilot, body, field=None):
    job, client = pilot
    resp = post_result(client, job.pk, body)
    assert resp.status_code == 400, resp.data
    if field is not None:
        assert field in resp.data, resp.data
    assert_untouched(job)
    return resp


@pytest.mark.django_db
class TestRequiredKeys:

    @pytest.mark.parametrize('field', NEW_FIELDS)
    def test_a_missing_field_is_400(self, pilot, field):
        body = pilot_body()
        del body[field]
        _refused(pilot, body, field)

    def test_an_unknown_key_beside_them_is_400(self, pilot):
        resp = _refused(pilot, pilot_body(card_name='x'))
        assert resp.data == {'detail': 'Unknown keys: card_name.'}

    def test_the_response_has_no_gate_need(self, pilot):
        job, client = pilot
        resp = post_result(client, job.pk, pilot_body())
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': 8102})


BAD_KINDS = ['10DE:2B85:00A1', '10de:2b85', '10de:2b85:00a1:00', '10de:2b85:00a', '10de-2b85-00a1',
             '10de:2b85:NONE', '', 5, None, ['10de:2b85:00a1']]
BAD_VERDICTS = ['testing', 'FITS', 'does-not-fit', '', None, True, 1]
BAD_SHARED = [-1, MAX_PEAK_MB + 1, True, '512', 512.0]


@pytest.mark.django_db
class TestFieldBounds:

    @pytest.mark.parametrize('value', BAD_KINDS, ids=[repr(v) for v in BAD_KINDS])
    def test_card_kind_form(self, pilot, value):
        _refused(pilot, pilot_body(card_kind=value), 'card_kind')

    @pytest.mark.parametrize('value', BAD_VERDICTS, ids=[repr(v) for v in BAD_VERDICTS])
    def test_verdict_choices(self, pilot, value):
        _refused(pilot, pilot_body(verdict=value), 'verdict')

    @pytest.mark.parametrize('value', BAD_SHARED, ids=[repr(v) for v in BAD_SHARED])
    def test_shared_peak_bounds(self, pilot, value):
        _refused(pilot, pilot_body(shared_peak_mb=value), 'shared_peak_mb')

    @pytest.mark.parametrize('value', [0, 40000, MAX_PEAK_MB])
    def test_shared_peak_is_stored_and_not_bounded_by_the_card(self, pilot, value):
        # Shared GPU memory is system RAM the driver maps, so it may exceed the card's total.
        job, client = pilot
        assert post_result(client, job.pk, pilot_body(shared_peak_mb=value)).status_code == 200
        assert record_of(job).shared_peak_mb == value

    def test_null_shared_peak_is_stored_as_null(self, pilot):
        job, client = pilot
        assert post_result(client, job.pk, pilot_body(shared_peak_mb=None)).status_code == 200
        assert record_of(job).shared_peak_mb is None


DRIVER_40 = '1234567890.ABCDEFGHIJ-klmnopqrst.0987654'
BAD_DRIVERS = [
    DRIVER_40 + 'x', '', ' 32.0.16.1692', '32.0.16.1692 ', '32.0.16.1692\n', '32 0 16', '32_0_16',
    '32.0.16.1692)\nLogD3D12RHI: Driver Version: 1.0', 'internal:32.0', '32.0;rm -rf', '<script>',
    '32.0.16.١', 'Unknown ', 5, 32.0, True, ['32.0'],
]


@pytest.mark.django_db
class TestDriverVersion:

    def test_forty_characters_are_stored_exactly(self, pilot):
        assert len(DRIVER_40) == 40
        job, client = pilot
        assert post_result(client, job.pk, pilot_body(driver_version=DRIVER_40)).status_code == 200
        assert record_of(job).driver_version == DRIVER_40

    @pytest.mark.parametrize('value', BAD_DRIVERS, ids=[f'bad{i}' for i in range(len(BAD_DRIVERS))])
    def test_refused(self, pilot, value):
        _refused(pilot, pilot_body(driver_version=value), 'driver_version')

    @pytest.mark.parametrize('value', ['Unknown', None], ids=['unknown', 'null'])
    def test_unknown_and_null_are_stored_as_blank(self, pilot, value):
        job, client = pilot
        assert post_result(client, job.pk, pilot_body(driver_version=value)).status_code == 200
        assert record_of(job).driver_version == ''

    def test_only_the_exact_unknown_literal_means_unknown(self, pilot):
        # UE writes ``Unknown``; another spelling is an ordinary version string under the pattern.
        job, client = pilot
        assert post_result(client, job.pk, pilot_body(driver_version='unknown')).status_code == 200
        assert record_of(job).driver_version == 'unknown'


@pytest.mark.django_db
class TestLowerBound:

    def test_fits_needs_a_null_lower_bound(self, pilot):
        resp = _refused(pilot, pilot_body(lower_bound_mb=8102), 'lower_bound_mb')
        assert resp.data['lower_bound_mb'] == ["Must be null for fits."]

    @pytest.mark.parametrize('bound', [None, 8101, 8103, 1024], ids=['null', 'below', 'above', 'floor'])
    def test_does_not_fit_needs_the_peak(self, pilot, bound):
        resp = _refused(pilot, dnf_body(lower_bound_mb=bound), 'lower_bound_mb')
        assert resp.data['lower_bound_mb'] == ["Must equal peak_mb for does_not_fit."]

    @pytest.mark.parametrize('bound', [1023, MAX_PEAK_MB + 1, True, '8102', 8102.0])
    def test_field_bounds(self, pilot, bound):
        _refused(pilot, dnf_body(lower_bound_mb=bound), 'lower_bound_mb')

    def test_does_not_fit_with_the_peak_is_stored(self, pilot):
        job, client = pilot
        resp = post_result(client, job.pk, dnf_body())
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': 8102})
        record = record_of(job)
        assert (record.verdict, record.lower_bound_mb, record.verdict_source) == ('does_not_fit', 8102, 'pilot')

    def test_fits_stores_a_null_lower_bound(self, pilot):
        job, client = pilot
        assert post_result(client, job.pk, pilot_body()).status_code == 200
        record = record_of(job)
        assert (record.verdict, record.lower_bound_mb, record.verdict_source) == ('fits', None, 'pilot')
