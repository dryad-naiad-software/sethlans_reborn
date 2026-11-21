# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Every reading of a launch is cut to the launch's card (GPU memory reader
D22, FR-GU-11, AC-22): pre-launch, per tick and post-exit, then the pilot
metrics see that card alone.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import phases, pilot
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict
from sethlans_worker_agent.unreal_executor.prelaunch import PreLaunch
from sethlans_worker_agent.unreal_executor.runner_types import VramTrace
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from sethlans_worker_agent.unreal_executor.zen_log import ZenLogResult
from tests.unit.worker.unreal._executor_fakes import outcome

JOB_KEY = 'pci:0000:41:00.0'     # the job's card, DXGI/reader index 1
OTHER_KEY = 'pci:0000:0a:00.0'   # another program's card, index 0
CHOICE = Choice(42, JOB_KEY, 'Radeon Test 24G', 1)


def card(used, ours=None):
    return GpuMem(1, 'Radeon Test 24G', 24560, used, ours, None, JOB_KEY)


def other(used, ours=None):
    return GpuMem(0, 'GeForce Test 16G', 16303, used, ours, None, OTHER_KEY)


JOB_PID = 4242                   # the launch's one Job Object member


def _per_pid(gpus):
    """Each card's ``ours`` as the job's own pid (FR-PC-ATT-1); ``None`` when no card is attributed."""
    if all(gpu.sethlans_used_mb is None for gpu in gpus):
        return None
    return {gpu.card_key: {} if gpu.sethlans_used_mb is None else {JOB_PID: gpu.sethlans_used_mb} for gpu in gpus}


def script(*rows):
    """One reading per row (pre, ticks..., post), ``taken_at`` 1, 2, 3, ..."""
    return [GpuMemoryReading(float(n), 'test-backend', tuple(gpus), measures_card=True, per_pid=_per_pid(gpus))
            for n, gpus in enumerate(rows, start=1)]


class Sampler:
    def __init__(self, readings):
        self.readings = list(readings)

    def fresh(self, max_age_s, deadline):
        return self.readings.pop(0)


class Job:
    ledger = Path('procs.json')
    output = Path('output')

    def log(self, phase, n):
        return Path(f'{phase}.{n}.log')

    def stdout(self, phase, n):
        return Path(f'{phase}.{n}.stdout.txt')


def runner(readings, card_key=None):
    """A ``PhaseRunner`` whose launch takes ``len(readings) - 2`` ticks, as ``process_runner`` would."""
    specs = []

    def run(spec, registry):
        specs.append(spec)
        trace = VramTrace()
        for _ in range(len(readings) - 2):
            trace.add(spec.vram_sample(1.0), frozenset({JOB_PID}))
        return outcome(vram=trace)

    config = phases.PhaseConfig(cache_dir='D:\\c', zen_port=8668, worker_sid=None, merged_names=[],
                                timeouts={'prep': 1, 'warmup': 2, 'render': 3}, hang_s=900, quit_grace_s=60,
                                log_max_bytes=10, log_flood_bytes_per_min=10)
    r = phases.PhaseRunner(config=config, registry=object(), job_dir=Job(), cancel=lambda: None, shutdown_event=None,
                           sampler=Sampler(readings), run=run,
                           prelaunch=lambda *a, **k: PreLaunch(ok=True, profile_reset=0, user_cvars=0),
                           verify_zen=lambda *a, **k: ZenLogResult(ok=True), loopback_factory=lambda *a, **k: object(),
                           card_key=card_key)
    return r, specs


TWO_CARDS = script((other(1000), card(300)), (other(1000), card(1300)), (other(1000), card(1550)),
                   (other(1000), card(310)))


class TestCut:

    def test_pre_tick_and_post_readings_hold_the_choices_card_only(self):
        r, specs = runner(TWO_CARDS, card_key=OTHER_KEY)   # the choice wins over the gated key
        run = r.launch('render', 1, SimpleNamespace(), sample=True, adapter=CHOICE)
        assert run.pre == {1: 300} and run.post == {1: 310} and specs[0].vram_baseline == {1: 300}
        assert run.pre_reading.gpus == (card(300),)
        assert run.outcome.vram.max_used == {1: 1550} and run.outcome.vram.samples == 2

    def test_a_pilot_prep_without_a_choice_is_cut_to_the_gated_card(self):
        r, _ = runner(TWO_CARDS, card_key=JOB_KEY)
        run = r.launch('prep', 1, SimpleNamespace(), sample=True)
        assert run.pre == {1: 300} and run.post == {1: 310} and run.outcome.vram.max_used == {1: 1550}

    def test_a_padded_key_of_the_same_address_still_matches(self):
        r, _ = runner(TWO_CARDS, card_key='pci:00000000:41:00.0')
        assert r.launch('prep', 1, SimpleNamespace(), sample=True).pre == {1: 300}

    def test_without_any_key_nothing_is_cut(self):
        r, _ = runner(TWO_CARDS)
        run = r.launch('prep', 1, SimpleNamespace(), sample=True)
        assert run.pre == {0: 1000, 1: 300} and run.outcome.vram.max_used == {0: 1000, 1: 1550}

    def test_a_reading_without_the_card_is_no_sample(self):
        readings = script((other(1000),), (other(1000), card(1300)), (other(5000),), (other(1000), card(1400)),
                          (other(1000), card(310)))
        r, specs = runner(readings, card_key=JOB_KEY)
        run = r.launch('prep', 1, SimpleNamespace(), sample=True)
        assert run.pre is None and run.pre_reading is None and specs[0].vram_baseline == {}
        assert run.outcome.vram.samples == 2 and run.outcome.vram.max_used == {1: 1400}
        assert run.outcome.vram.max_gap_s == 2.0 and run.post == {1: 310}
        with pytest.raises(LocalConflict):   # no pre-launch sample: the pilot cannot measure
            pilot.measure([run], None)


# AC-22: another program takes 2 GB on the other card, then frees it.
ALLOCATES_DURING = script((other(1000), card(300)), (other(1000), card(1300, 1000)),
                          (other(3048, 2900), card(1550, 1200)), (other(1000), card(1500)), (other(1000), card(310)))
FREES_DURING = script((other(3048), card(300)), (other(3048), card(1300, 1000)), (other(1000), card(1550, 1200)),
                      (other(1000), card(1500)), (other(3048), card(310)))


def _measure(readings, card_key, adapter):
    r, _ = runner(readings, card_key=card_key)
    r.launch('render', 1, SimpleNamespace(), sample=True, adapter=adapter)
    return pilot.measure(r.runs, None)


class TestAc22:

    @pytest.mark.parametrize('readings', [ALLOCATES_DURING, FREES_DURING], ids=['allocates', 'frees'])
    def test_the_other_card_changes_neither_the_peak_nor_trips_the_freed_rule(self, readings):
        metrics = _measure(readings, JOB_KEY, CHOICE)
        assert metrics.card_delta_peak_mb == 1250 and metrics.gpu_index == 1 and metrics.peak_mb == 1250
        assert metrics.baseline_used_mb == 300 and metrics.process_peak_mb == 1200

    def test_uncut_the_allocation_would_have_been_the_peak(self):
        """The counterfactual that makes the test above meaningful (the override: no key, nothing cut)."""
        metrics = _measure(ALLOCATES_DURING, None, None)
        assert metrics.card_delta_peak_mb == 2048 and metrics.gpu_index == 0 and metrics.process_peak_mb == 2900

    def test_uncut_the_free_would_have_tripped_the_freed_rule(self):
        with pytest.raises(LocalConflict, match='freed by another program'):
            _measure(FREES_DURING, None, None)

    def test_the_process_peak_comes_from_the_jobs_card_only(self):
        metrics = _measure(ALLOCATES_DURING, JOB_KEY, CHOICE)
        values = pilot.summary_values(metrics, [0, 50], 'Radeon Test 24G', 'c' * 64)
        assert values['pilot_process_peak_mb'] == 1200
