# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An Unreal job's RAM need (render per card FR-PC-RAM-4, D9; AC-5): the measured figures from the poll
payload, the defaults where a part is not measured, and the warm-up a cold **or stale** slot plans.
"""
import pytest

from sethlans_worker_agent.unreal_executor import cvars, farm_defaults, job_params, job_ram, reserve_local, warmup
from tests.unit.worker.unreal._executor_fakes import job, params

RAM = {'prep': {'ws_mb': 4000, 'commit_mb': 10500},
       'warmup': {'ws_mb': 12000, 'commit_mb': 30000, 'cold': True},
       'render': {'ws_mb': 7500, 'commit_mb': 17600}}
DEFAULTS = dict(default_ws_mb=16384, default_commit_mb=32768)


def measured(**changes):
    raw = {name: (dict(value) if value is not None else None) for name, value in RAM.items()}
    raw.update(changes)
    return job_ram.parse_measured(raw)


class TestParse:

    def test_the_manager_shape_is_read(self):
        ram = measured()
        assert ram.prep == job_ram.PhaseRam(4000, 10500) and ram.render == job_ram.PhaseRam(7500, 17600)
        assert ram.warmup == job_ram.PhaseRam(12000, 30000) and ram.warmup_cold is True
        assert measured(warmup=None).warmup is None and job_ram.parse_measured(None) is None

    @pytest.mark.parametrize('raw', [
        [], {'prep': RAM['prep'], 'render': RAM['render']}, dict(RAM, extra=1),
        dict(RAM, prep={'ws_mb': 1}), dict(RAM, prep={'ws_mb': 0, 'commit_mb': 1}),
        dict(RAM, render={'ws_mb': True, 'commit_mb': 1}), dict(RAM, render={'ws_mb': 1.5, 'commit_mb': 1}),
        dict(RAM, warmup={'ws_mb': 1, 'commit_mb': 1}), dict(RAM, warmup={'ws_mb': 1, 'commit_mb': 1, 'cold': 1}),
        dict(RAM, prep={'ws_mb': 1, 'commit_mb': 1048577}),
    ])
    def test_any_other_shape_is_refused(self, raw):
        with pytest.raises(ValueError):
            job_ram.parse_measured(raw)

    def test_job_params_carries_it_for_a_normal_job(self):
        assert params(unreal_measured_ram=RAM).measured_ram == measured()
        assert params().measured_ram is None   # absent: no measurement

    def test_job_params_names_the_field_of_a_bad_value(self):
        with pytest.raises(job_params.JobDataError, match='unreal_measured_ram'):
            job_params.validate_job_data(job(unreal_measured_ram=dict(RAM, extra=1)))

    def test_a_pilot_must_not_carry_one(self):
        with pytest.raises(job_params.JobDataError, match='unreal_measured_ram'):
            job_params.validate_job_data(job(kind='pilot', unreal_measured_ram=RAM))


class TestNeed:

    def test_a_warm_slot_needs_the_larger_of_prep_and_render(self):
        need = job_ram.need(measured(), False, 20464, **DEFAULTS)
        assert (need.ws_mb, need.commit_mb, need.source) == (7500, 17600, 'measured')

    def test_a_planned_warm_up_adds_the_measured_cold_warm_up(self):
        need = job_ram.need(measured(), True, 20464, **DEFAULTS)
        assert (need.ws_mb, need.commit_mb, need.source, need.warmup) == (12000, 30000, 'measured', True)

    @pytest.mark.parametrize('warm', [None, {'ws_mb': 5000, 'commit_mb': 9000, 'cold': False}])
    def test_a_warm_up_not_measured_cold_uses_the_defaults_for_that_part(self, warm):
        need = job_ram.need(measured(warmup=warm), True, 20464, **DEFAULTS)
        assert (need.ws_mb, need.commit_mb, need.source) == (16384, 16384 + 20464, 'measured+default')

    def test_no_measurement_uses_the_defaults_and_the_card_size(self):
        def figures(*args):
            need = job_ram.need(*args, **DEFAULTS)
            return need.ws_mb, need.commit_mb, need.source
        assert figures(None, False, 20464) == (16384, 36848, 'default')
        assert figures(None, True, 8192) == (16384, 32768, 'default')   # the commit floor
        assert figures(None, False, 0) == (16384, 32768, 'default')

    def test_a_bad_card_size_raises(self):
        with pytest.raises(ValueError):
            job_ram.need(None, False, None, **DEFAULTS)


class TestWarmupPlanned:
    """Exactly the flow's test (``warmup.decide``): a missing pair, other farm defaults or other CVars."""

    @pytest.fixture
    def entry(self, tmp_path):
        p = params()
        sha = cvars.warmup_cvars_sha256(p.merged)
        warmup.record_success(warmup.marker_path(tmp_path, p.overlay_sha256, p.engine_build, 0), p.map, p.sequence, sha)
        return tmp_path

    def test_the_warm_slot_plans_none_and_another_slot_plans_one(self, entry):
        """AC-5: slot 0's marker does not mark slot 1 warm."""
        assert reserve_local.warmup_planned(params(), entry, 0) is False
        assert reserve_local.warmup_planned(params(), entry, 1) is True

    def test_another_pair_plans_one(self, entry):
        changed = params(render_settings={'unreal.map': '/Game/Maps/Other', 'unreal.sequence': '/Game/Seq/Edit',
                                          'unreal.preset': '/Game/Presets/MRG', 'unreal.output_format': 'PNG'})
        assert reserve_local.warmup_planned(changed, entry, 0) is True

    def test_a_stale_marker_plans_one(self, entry, monkeypatch):
        """The reviewer's note: other farm defaults or other shader CVars warm up although the pair is listed."""
        monkeypatch.setattr(farm_defaults, 'FARM_DEFAULTS_VERSION', farm_defaults.FARM_DEFAULTS_VERSION + 1)
        assert reserve_local.warmup_planned(params(), entry, 0) is True
        monkeypatch.undo()
        overrides = {'r.Streaming.PoolSize': '9000', 'r.Shadow.Virtual.Enable': '0'}
        other = job()
        other['asset']['project_details']['unreal_cvar_overrides'] = overrides
        p = job_params.validate_job_data(other)
        assert cvars.warmup_cvars_sha256(p.merged) != cvars.warmup_cvars_sha256(params().merged)
        assert reserve_local.warmup_planned(p, entry, 0) is True

    def test_a_pilot_always_and_shots_never(self, entry):
        assert reserve_local.warmup_planned(params(kind='pilot'), entry, 0) is True
        assert reserve_local.warmup_planned(params(kind='shots', frame=0), None, 3) is False

    def test_without_a_cache_entry_a_warm_up_is_planned(self):
        assert reserve_local.warmup_planned(params(), None, 0) is True
