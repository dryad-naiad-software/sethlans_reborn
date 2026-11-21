# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A job's memory figures (render per card FR-PC-RAM-3, FR-PC-ATT-2): the summary's per-phase RAM keys
and the job's own card memory, and the pilot body's ``ram`` and ``ram_process_commit_peak_mb``.
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import job_memory
from sethlans_worker_agent.unreal_executor.runner_ram import PhaseRam
from sethlans_worker_agent.unreal_executor.runner_types import VramTrace
from tests.unit.worker.unreal._executor_fakes import outcome

KEYS = ('ram_prep_ws_mb', 'ram_prep_commit_mb', 'ram_warmup_ws_mb', 'ram_warmup_commit_mb', 'ram_render_ws_mb',
        'ram_render_commit_mb', 'ram_warmup_cold', 'vram_job_peak_mb', 'vram_job_other_cards_mb')


def run(phase, ws=None, commit=None, process=None, vram=None, ram=True, scw=False):
    figures = PhaseRam(ws, commit, process) if ram else None
    result = SimpleNamespace(phase=phase, outcome=outcome(ram=figures, vram=vram or VramTrace()))
    result.outcome.shader_workers = scw   # a ShaderCompileWorker was a member of the launch
    return result


PILOT = [run('prep', 3000, 5000, 4000), run('warmup', 6000, 20000, 15000, scw=True),
         run('render', 9000, 28000, 27000)]


class TestSummary:

    def test_no_launch_gives_every_key_empty(self):
        values = job_memory.summary_values(())
        assert tuple(values) == KEYS
        assert values['ram_warmup_cold'] == 0
        assert all(values[key] == '' for key in KEYS if key != 'ram_warmup_cold')

    def test_each_phase_has_its_own_figures(self):
        values = job_memory.summary_values(PILOT)
        assert (values['ram_prep_ws_mb'], values['ram_prep_commit_mb']) == (3000, 5000)
        assert (values['ram_warmup_ws_mb'], values['ram_warmup_commit_mb']) == (6000, 20000)
        assert (values['ram_render_ws_mb'], values['ram_render_commit_mb']) == (9000, 28000)
        assert values['ram_warmup_cold'] == 1

    def test_a_retry_counts_the_largest_launch_of_the_phase(self):
        runs = [run('prep', 3000, 5000), run('render', 9000, 20000), run('warmup', 4000, 9000),
                run('render', 7000, 29000)]
        values = job_memory.summary_values(runs)
        assert (values['ram_render_ws_mb'], values['ram_render_commit_mb']) == (9000, 29000)
        assert values['ram_warmup_cold'] == 0

    def test_one_unknown_launch_makes_its_phases_figure_unknown(self):
        runs = [run('prep', 3000, None), run('render', None, 20000), run('render', 7000, 29000)]
        values = job_memory.summary_values(runs)
        assert (values['ram_prep_ws_mb'], values['ram_prep_commit_mb']) == (3000, '')
        assert (values['ram_render_ws_mb'], values['ram_render_commit_mb']) == ('', 29000)

    def test_an_outcome_without_figures_is_unknown(self):
        values = job_memory.summary_values([run('prep', ram=False)])
        assert values['ram_prep_ws_mb'] == '' and values['ram_prep_commit_mb'] == ''

    def test_a_phase_that_did_not_run_is_empty(self):
        values = job_memory.summary_values([run('prep', 3000, 5000)])   # shots: the prep alone
        assert values['ram_render_ws_mb'] == '' and values['ram_warmup_commit_mb'] == ''

    def test_the_jobs_card_memory_comes_from_the_last_render(self):
        first = VramTrace(job_peak_mb=9000, job_other_cards_mb=40)
        last = VramTrace(job_peak_mb=7100, job_other_cards_mb=12)
        values = job_memory.summary_values([run('render', vram=first), run('render', vram=last)])
        assert (values['vram_job_peak_mb'], values['vram_job_other_cards_mb']) == (7100, 12)

    @pytest.mark.parametrize('trace', [VramTrace(job_peak_mb=7100, job_other_cards_mb=0, job_unknown=True),
                                       VramTrace()], ids=['unattributed-sample', 'no-first-output'])
    def test_unknown_card_memory_is_empty(self, trace):
        values = job_memory.summary_values([run('render', vram=trace)])
        assert (values['vram_job_peak_mb'], values['vram_job_other_cards_mb']) == ('', '')


class TestWarmupCold:
    """D8: cold exactly when a ShaderCompileWorker was among a warm-up launch's members."""

    def test_a_shader_worker_in_the_warm_up_is_cold(self):
        assert job_memory.summary_values([run('prep'), run('warmup', scw=True), run('render')])['ram_warmup_cold'] == 1

    def test_no_shader_worker_is_warm(self):
        assert job_memory.summary_values([run('prep'), run('warmup'), run('render')])['ram_warmup_cold'] == 0

    def test_a_new_version_on_a_warm_slot_is_warm(self):
        """Its marker is new (the warm-up ran), but the slot's Zen and DDC cache held every shader: no SCW."""
        runs = [run('prep', 4000, 5000, 4000), run('warmup', 7000, 17000, 16000), run('render', 7500, 17600, 17000)]
        assert job_memory.summary_values(runs)['ram_warmup_cold'] == 0
        assert job_memory.pilot_ram(runs)[0]['warmup']['cold'] is False

    def test_shader_workers_in_prep_or_render_do_not_count(self):
        runs = [run('prep', scw=True), run('warmup'), run('render', scw=True)]
        assert job_memory.summary_values(runs)['ram_warmup_cold'] == 0

    def test_a_retry_warm_up_that_compiled_is_cold(self):
        runs = [run('prep'), run('warmup'), run('render'), run('warmup', scw=True), run('render')]
        assert job_memory.summary_values(runs)['ram_warmup_cold'] == 1


class TestPilotBody:

    def test_every_figure_known(self):
        ram, process = job_memory.pilot_ram(PILOT)
        assert ram == {'prep': {'ws_mb': 3000, 'commit_mb': 5000},
                       'warmup': {'ws_mb': 6000, 'commit_mb': 20000, 'cold': True},
                       'render': {'ws_mb': 9000, 'commit_mb': 28000}}
        assert process == 27000

    def test_a_warm_warm_up_says_so(self):
        ram, _ = job_memory.pilot_ram([PILOT[0], run('warmup', 6000, 9000, 8000), PILOT[2]])
        assert ram['warmup']['cold'] is False

    def test_no_warm_up_sends_null(self):
        ram, _ = job_memory.pilot_ram([PILOT[0], PILOT[2]])
        assert ram['warmup'] is None and ram['render'] == {'ws_mb': 9000, 'commit_mb': 28000}

    @pytest.mark.parametrize('runs', [
        [run('prep', None, 5000, 4000), PILOT[1], PILOT[2]],
        [PILOT[0], run('warmup', 6000, None, 15000), PILOT[2]],
        [PILOT[0], PILOT[1]],
    ], ids=['prep-ws', 'warmup-commit', 'no-render'])
    def test_any_unknown_figure_sends_null(self, runs):
        assert job_memory.pilot_ram(runs)[0] is None

    def test_the_process_peak_is_unknown_when_any_launch_is(self):
        runs = [PILOT[0], run('warmup', 6000, 20000, None), PILOT[2]]
        assert job_memory.pilot_ram(runs)[1] is None

    def test_the_process_peak_never_exceeds_the_largest_commit(self):
        """The manager's rule: ``ram_process_commit_peak_mb`` <= the largest ``commit_mb``."""
        ram, process = job_memory.pilot_ram(PILOT)
        assert process <= max(phase['commit_mb'] for phase in ram.values() if phase)
