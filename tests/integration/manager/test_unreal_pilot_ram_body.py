# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the RAM fields of ``POST /api/jobs/{id}/pilot-result/``
(per-card spec FR-PC-RAM-3, FR-PC-MGR-1): stored as sent, the strict
schema with a minimum of 1, the process peak bounded by the largest
commit, and no bound from ``gpu.total_mb``.
"""

import copy

import pytest

from shared.unreal.vram_gate import MAX_PEAK_MB

from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import PILOT_RAM, claimed_pilot, pilot_body, post_result, record_of

RAM_FIELDS = ('ram', 'ram_process_commit_peak_mb', 'shared_machine')


@pytest.fixture
def pilot(admin_client, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, unreal_asset(), worker, client), client


def _ram(**phases):
    ram = copy.deepcopy(PILOT_RAM)
    ram.update(phases)
    return ram


def _refused(pilot, body):
    """POST ``body``; assert a 400 that changed nothing; return the errors."""
    job, client = pilot
    resp = post_result(client, job.pk, body)
    assert resp.status_code == 400, resp.data
    job.refresh_from_db()
    record = record_of(job)
    assert (job.status, record.peak_mb, record.ram, record.shared_machine) == ('RENDERING', None, None, None)
    return resp.data


def _stored(pilot, body):
    job, client = pilot
    resp = post_result(client, job.pk, body)
    assert resp.status_code == 200, resp.data
    return record_of(job)


@pytest.mark.django_db
class TestStored:

    def test_the_three_fields_are_stored_as_sent(self, pilot):
        record = _stored(pilot, pilot_body())
        assert record.ram == PILOT_RAM
        assert (record.ram_process_commit_peak_mb, record.shared_machine) == (18900, False)

    def test_shared_machine_true_is_stored(self, pilot):
        assert _stored(pilot, pilot_body(shared_machine=True)).shared_machine is True

    def test_unknown_figures_are_stored_as_null(self, pilot):
        record = _stored(pilot, pilot_body(ram=None, ram_process_commit_peak_mb=None))
        assert (record.ram, record.ram_process_commit_peak_mb, record.shared_machine) == (None, None, False)

    def test_a_null_warmup_is_stored(self, pilot):
        ram = _ram(warmup=None)
        assert _stored(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=20700)).ram == ram

    @pytest.mark.parametrize('ram, process', [
        (None, 18900), (PILOT_RAM, None),
    ], ids=['no-ram', 'no-process-peak'])
    def test_the_process_bound_applies_only_when_both_are_set(self, pilot, ram, process):
        assert _stored(pilot, pilot_body(ram=copy.deepcopy(ram), ram_process_commit_peak_mb=process)).ram == ram

    def test_one_megabyte_is_the_minimum_accepted(self, pilot):
        ones = {'ws_mb': 1, 'commit_mb': 1}
        ram = {'prep': dict(ones), 'warmup': dict(ones, cold=False), 'render': dict(ones)}
        record = _stored(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=1))
        assert (record.ram, record.ram_process_commit_peak_mb) == (ram, 1)

    def test_ram_figures_are_not_bounded_by_the_card(self, pilot):
        """gpu.total_mb is 32607; RAM commit above it is normal (D8)."""
        big = {'ws_mb': MAX_PEAK_MB, 'commit_mb': MAX_PEAK_MB}
        ram = {'prep': dict(big), 'warmup': None, 'render': dict(big)}
        record = _stored(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=MAX_PEAK_MB))
        assert record.ram_process_commit_peak_mb == MAX_PEAK_MB


@pytest.mark.django_db
class TestProcessPeakBound:

    @pytest.mark.parametrize('ram, process', [
        (_ram(), 24600),  # the warm-up's commit is the largest
        (_ram(warmup=None), 20700),  # without a warm-up, the render's
        (_ram(warmup=None, prep={'ws_mb': 5200, 'commit_mb': 30000}), 30000),
    ], ids=['warmup-largest', 'render-largest', 'prep-largest'])
    def test_equal_to_the_largest_commit_is_accepted(self, pilot, ram, process):
        assert _stored(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=process)).ram == ram

    @pytest.mark.parametrize('ram, largest', [
        (_ram(), 24600), (_ram(warmup=None), 20700),
    ], ids=['warmup-largest', 'render-largest'])
    def test_above_the_largest_commit_is_refused_on_its_field(self, pilot, ram, largest):
        errors = _refused(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=largest + 1))
        assert errors == {'ram_process_commit_peak_mb': [f'Must not exceed the largest ram commit_mb ({largest}).']}


PHASE_VALUES = [0, -1, MAX_PEAK_MB + 1, True, False, '5200', 5200.0, None, [5200], {'mb': 5200}]
TOP_CASES = [
    ('ram', 'x'), ('ram', 5), ('ram', True), ('ram', []),
    ('ram_process_commit_peak_mb', 0), ('ram_process_commit_peak_mb', -1),
    ('ram_process_commit_peak_mb', MAX_PEAK_MB + 1), ('ram_process_commit_peak_mb', True),
    ('ram_process_commit_peak_mb', '18900'), ('ram_process_commit_peak_mb', 18900.0),
    ('shared_machine', None), ('shared_machine', 0), ('shared_machine', 1), ('shared_machine', 'false'),
    ('shared_machine', []),
]


@pytest.mark.django_db
class TestStrictSchema:

    @pytest.mark.parametrize('field', RAM_FIELDS)
    def test_each_field_is_required(self, pilot, field):
        body = pilot_body()
        del body[field]
        errors = _refused(pilot, body)
        assert set(errors) == {field} and errors[field][0].code == 'required'

    @pytest.mark.parametrize('field, value', TOP_CASES, ids=[f'{f}-{i}' for i, (f, _v) in enumerate(TOP_CASES)])
    def test_bad_top_level_values_are_refused_on_their_field(self, pilot, field, value):
        assert set(_refused(pilot, pilot_body(**{field: value}))) == {field}

    @pytest.mark.parametrize('phase', ['prep', 'warmup', 'render'])
    @pytest.mark.parametrize('figure', ['ws_mb', 'commit_mb'])
    @pytest.mark.parametrize('value', PHASE_VALUES, ids=[repr(v) for v in PHASE_VALUES])
    def test_bad_figures_are_refused(self, pilot, phase, figure, value):
        ram = _ram()
        ram[phase][figure] = value
        errors = _refused(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=None))
        assert set(errors) == {'ram'} and set(errors['ram'][phase]) == {figure}

    @pytest.mark.parametrize('phase', ['prep', 'render'])
    def test_only_the_warmup_may_be_null(self, pilot, phase):
        errors = _refused(pilot, pilot_body(ram=_ram(**{phase: None}), ram_process_commit_peak_mb=None))
        assert set(errors['ram']) == {phase}

    @pytest.mark.parametrize('key', ['prep', 'warmup', 'render'])
    def test_every_phase_key_is_required(self, pilot, key):
        ram = _ram()
        del ram[key]
        errors = _refused(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=None))
        assert errors['ram'][key][0].code == 'required'

    @pytest.mark.parametrize('phase, key', [
        ('prep', 'ws_mb'), ('prep', 'commit_mb'), ('render', 'commit_mb'), ('warmup', 'cold'), ('warmup', 'ws_mb'),
    ])
    def test_every_phase_figure_is_required(self, pilot, phase, key):
        ram = _ram()
        del ram[phase][key]
        errors = _refused(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=None))
        assert errors['ram'][phase][key][0].code == 'required'

    @pytest.mark.parametrize('cold', [None, 0, 1, 'true', 'True'])
    def test_cold_is_a_strict_bool(self, pilot, cold):
        ram = _ram()
        ram['warmup']['cold'] = cold
        errors = _refused(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=None))
        assert set(errors['ram']['warmup']) == {'cold'}

    @pytest.mark.parametrize('phase', ['prep', 'render'])
    def test_cold_only_in_the_warmup(self, pilot, phase):
        ram = _ram()
        ram[phase]['cold'] = True
        errors = _refused(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=None))
        assert errors['ram'][phase] == {'cold': ['Unknown key.']}

    def test_an_extra_ram_key_is_an_unknown_key(self, pilot):
        ram = _ram(idle={'ws_mb': 1, 'commit_mb': 1})
        assert _refused(pilot, pilot_body(ram=ram)) == {'detail': 'Unknown keys: ram.idle.'}

    def test_an_extra_phase_key_is_refused_on_that_key(self, pilot):
        ram = _ram()
        ram['render']['peak_mb'] = 9
        errors = _refused(pilot, pilot_body(ram=ram, ram_process_commit_peak_mb=None))
        assert errors == {'ram': {'render': {'peak_mb': ['Unknown key.']}}}
