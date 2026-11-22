# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A job's ``unreal_machine_results`` (Unreal per-machine memory test FR-MT-G-1, D4, D7, D11) and a pilot's
``unreal.card_kind`` and ``unreal.draft`` (FR-MT-R-5, OQ-1).

``machine_results.parse`` takes a list of at most 4 entries for a render or audio job (``[]`` allowed, null
refused) and null or ``[]`` for a pilot or ``shots`` job; every entry has exactly six keys, a positive
``measurement_id``, a unique ``KIND_RE`` kind, a peak in ``1..MAX_PEAK_MB``, ``shared_peak_mb`` null or in
``0..MAX_PEAK_MB`` and ``driver_version`` null or a string of at most 64 characters.
"""
import pytest

from sethlans_worker_agent.unreal_executor import job_params, machine_results, queue_prep
from sethlans_worker_agent.unreal_executor.job_params import JobDataError, validate_job_data
from shared.unreal.vram_gate import MAX_PEAK_MB
from tests.unit.worker.unreal._executor_fakes import KIND, SETTINGS, job, machine_result
from tests.unit.worker.unreal.test_ram_need import RAM

OTHER = '1002:744c:00c8'
FIELD = 'unreal_machine_results'


def _error(kind, raw):
    with pytest.raises(ValueError) as info:
        machine_results.parse(kind, raw)
    return str(info.value)


class TestKeyedJobs:

    @pytest.mark.parametrize('kind', ['render', 'audio'])
    def test_an_empty_list_is_accepted(self, kind):
        """No result on this machine: the gate then finds no card (FR-MT-G-2)."""
        assert machine_results.parse(kind, []) == ()

    @pytest.mark.parametrize('kind', ['render', 'audio'])
    @pytest.mark.parametrize('raw', [None, {}, 'x', 7, (machine_result(),)])
    def test_anything_but_a_list_is_refused(self, kind, raw):
        assert _error(kind, raw) == f"a {kind} job needs a list of this machine's results"

    def test_every_field_is_read(self):
        raw = machine_result(peak=13689, kind=OTHER, measurement_id=9, ram=RAM, shared_peak_mb=512,
                             driver_version='32.0.22029.1019')
        [result] = machine_results.parse('render', [raw])
        assert (result.measurement_id, result.card_kind, result.peak_mb) == (9, OTHER, 13689)
        assert (result.shared_peak_mb, result.driver_version) == (512, '32.0.22029.1019')
        assert result.ram.render.ws_mb == RAM['render']['ws_mb'] and result.ram.warmup_cold is True

    def test_four_kinds_are_accepted_five_are_refused(self):
        kinds = ['10de:2b85:00a1', '10de:2b85:none', '1002:744c:00c8', '1002:744c:00cc', '8086:56a0:0008']
        four = [machine_result(kind=k, measurement_id=i + 1) for i, k in enumerate(kinds[:4])]
        assert [r.card_kind for r in machine_results.parse('audio', four)] == kinds[:4]
        five = four + [machine_result(kind=kinds[4], measurement_id=5)]
        assert _error('render', five) == 'at most 4 results'

    def test_a_kind_listed_twice_is_refused(self):
        twice = [machine_result(measurement_id=1), machine_result(measurement_id=2, peak=9000)]
        assert _error('render', twice) == 'a card kind is listed twice'

    @pytest.mark.parametrize('kind', ['10de:2b85:none', '0000:0000:0000', 'ffff:ffff:ffff'])
    def test_kind_forms_that_match(self, kind):
        assert machine_results.parse('render', [machine_result(kind=kind)])[0].card_kind == kind

    @pytest.mark.parametrize('kind', ['10DE:2b85:00a1', '10de:2b85', '10de:2b85:a1', '10de:2b85:NONE', '',
                                      '10de:2b85:00a1\n', ' 10de:2b85:00a1', None, 0x10de])
    def test_kind_forms_that_do_not(self, kind):
        assert _error('render', [machine_result(kind=kind)]) == '[0].card_kind must be a card kind (vvvv:dddd:rrrr)'

    @pytest.mark.parametrize('value', [0, -1, True, '1', 1.0, None])
    def test_measurement_id_must_be_a_positive_int(self, value):
        assert _error('render', [machine_result(measurement_id=value)]) == '[0].measurement_id must be an integer > 0'

    @pytest.mark.parametrize('peak,ok', [(1, True), (MAX_PEAK_MB, True), (0, False), (MAX_PEAK_MB + 1, False),
                                         (True, False), (7930.0, False), ('7930', False), (None, False)])
    def test_peak_bounds(self, peak, ok):
        raw = [machine_result(peak=peak)]
        if ok:
            assert machine_results.parse('render', raw)[0].peak_mb == peak
        else:
            assert _error('render', raw) == f'[0].peak_mb must be an integer in [1, {MAX_PEAK_MB}]'

    @pytest.mark.parametrize('shared,ok', [(None, True), (0, True), (MAX_PEAK_MB, True), (-1, False),
                                           (MAX_PEAK_MB + 1, False), (False, False), (1.5, False)])
    def test_shared_peak_bounds(self, shared, ok):
        raw = [machine_result(shared_peak_mb=shared)]
        if ok:
            assert machine_results.parse('render', raw)[0].shared_peak_mb == shared
        else:
            assert _error('render', raw) == f'[0].shared_peak_mb must be null or an integer in [0, {MAX_PEAK_MB}]'

    @pytest.mark.parametrize('driver,ok', [(None, True), ('', True), ('x' * 64, True), ('x' * 65, False),
                                           (32, False), (['1'], False)])
    def test_driver_version_bounds(self, driver, ok):
        raw = [machine_result(driver_version=driver)]
        if ok:
            assert machine_results.parse('render', raw)[0].driver_version == driver
        else:
            assert _error('render', raw) == ('[0].driver_version must be null or a string of at most 64 '
                                             'characters')

    @pytest.mark.parametrize('change', [{'extra': 1}, {'ram': 'drop'}, {'driver_version': 'drop'}])
    def test_exactly_the_six_keys(self, change):
        raw = machine_result()
        for name, value in change.items():
            if value == 'drop':
                del raw[name]
            else:
                raw[name] = value
        assert _error('render', [raw]).startswith('[0] must have exactly the keys ')

    @pytest.mark.parametrize('entry', [None, [], 'x', 7])
    def test_an_entry_must_be_an_object(self, entry):
        assert _error('audio', [machine_result(), entry]) == '[1] must be an object'

    def test_a_bad_ram_names_its_entry(self):
        raw = [machine_result(), machine_result(kind=OTHER, ram=dict(RAM, extra=1))]
        assert _error('render', raw).startswith('[1].ram: ')

    def test_the_error_names_the_entry_by_index(self):
        raw = [machine_result(), machine_result(kind=OTHER, peak=0)]
        assert _error('render', raw).startswith('[1].peak_mb ')


class TestUnkeyedJobs:

    @pytest.mark.parametrize('kind', ['pilot', 'shots'])
    @pytest.mark.parametrize('raw', [None, []])
    def test_null_or_empty_is_accepted(self, kind, raw):
        assert machine_results.parse(kind, raw) == ()

    @pytest.mark.parametrize('kind', ['pilot', 'shots'])
    @pytest.mark.parametrize('raw', [[machine_result()], {}, 'x', 0])
    def test_anything_else_is_refused(self, kind, raw):
        assert _error(kind, raw) == f'must be null for a {kind} job'


class TestForKind:

    def test_the_result_of_the_kind_or_none(self):
        results = machine_results.parse('render', [machine_result(), machine_result(kind=OTHER, measurement_id=2)])
        assert machine_results.for_kind(results, OTHER).measurement_id == 2
        assert machine_results.for_kind(results, KIND).measurement_id == 1
        assert machine_results.for_kind(results, '8086:56a0:0008') is None
        assert machine_results.for_kind(results, None) is None and machine_results.for_kind((), KIND) is None


class TestJobParams:

    @pytest.mark.parametrize('kind', ['render', 'audio'])
    def test_an_absent_field_is_refused_for_a_keyed_job(self, kind):
        data = job(kind=kind) if kind == 'render' else _audio()
        del data[FIELD]
        with pytest.raises(JobDataError) as info:
            validate_job_data(data)
        assert info.value.field == FIELD

    def test_an_empty_list_reaches_the_params(self):
        assert validate_job_data(job(unreal_machine_results=[])).machine_results == ()

    @pytest.mark.parametrize('kind,frame', [('pilot', 1000), ('shots', 0)])
    def test_an_absent_or_empty_field_is_fine_for_a_pilot_or_shots(self, kind, frame):
        data = job(kind=kind, frame=frame)
        data.pop(FIELD)
        assert validate_job_data(data).machine_results == ()
        assert validate_job_data(job(kind=kind, frame=frame, unreal_machine_results=[])).machine_results == ()

    def test_the_old_fields_are_gone(self):
        p = validate_job_data(job())
        assert not hasattr(p, 'measured_peak_mb') and not hasattr(p, 'measured_ram')

    def test_card_kind_is_empty_for_any_job_but_a_pilot(self):
        assert validate_job_data(job()).card_kind == ''
        assert validate_job_data(job(kind='pilot')).card_kind == KIND


def _audio():
    from tests.unit.worker.unreal.sequence._pieces import audio_data
    return audio_data()


def _pilot(**extra):
    return job(kind='pilot', render_settings=dict(SETTINGS, **extra))


class TestPilotSettings:

    def test_a_pilot_needs_its_card_kind(self):
        with pytest.raises(JobDataError) as info:
            validate_job_data(_pilot(**{'unreal.card_kind': None}))
        assert info.value.field == 'render_settings.unreal.card_kind'

    @pytest.mark.parametrize('value', ['10DE:2B85:00A1', '10de:2b85', 'none', '', 7, ['10de:2b85:00a1']])
    def test_a_card_kind_outside_the_form_is_refused(self, value):
        with pytest.raises(JobDataError) as info:
            validate_job_data(_pilot(**{'unreal.card_kind': value}))
        assert info.value.field == 'render_settings.unreal.card_kind'

    def test_a_kind_without_a_revision_is_accepted(self):
        assert validate_job_data(_pilot(**{'unreal.card_kind': '10de:2b85:none'})).card_kind == '10de:2b85:none'

    def test_only_a_pilot_may_carry_a_card_kind(self):
        """D4: ``unreal.card_kind`` is a pilot's manager-only key; a render job carrying it is refused."""
        with pytest.raises(JobDataError) as info:
            validate_job_data(job(render_settings=dict(SETTINGS, **{'unreal.card_kind': KIND})))
        assert info.value.field == 'render_settings'

    def test_a_pilot_accepts_a_draft_and_sends_its_cvars(self):
        """OQ-1: a draft is part of the key a pilot measures; its CVars join the merged list like a piece's."""
        draft = {'screen_percentage': 50, 'motion_blur': False}
        p = validate_job_data(_pilot(**{'unreal.draft': draft}))
        assert p.seq.draft == (('motion_blur', False), ('screen_percentage', 50))
        merged = dict(p.merged)
        assert merged['r.ScreenPercentage'] == '50' and merged['r.MotionBlurQuality'] == '0'

    def test_a_draft_pilots_prep_params_carry_the_draft(self, tmp_path):
        p = validate_job_data(_pilot(**{'unreal.draft': {'screen_percentage': 50}}))
        data = queue_prep.prep_params(p, tmp_path, tmp_path / 'r.json', tmp_path)
        assert data['draft'] == {'screen_percentage': 50} and data['draft_cvars'] == ['r.ScreenPercentage']
        plain = queue_prep.prep_params(validate_job_data(_pilot()), tmp_path, tmp_path / 'r.json', tmp_path)
        assert 'draft' not in plain and 'draft_cvars' not in plain

    def test_a_bad_draft_on_a_pilot_names_the_draft_key(self):
        with pytest.raises(job_params.JobDataError) as info:
            validate_job_data(_pilot(**{'unreal.draft': {'screen_percentage': 9}}))
        assert info.value.field == 'render_settings.unreal.draft.screen_percentage'
