# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
D9's samples from a launch's ``VramTrace`` and the job's outcome over its render launches
(Unreal per-machine memory test D9, FR-MT-S-2).

The trace feeds its ``spill`` watch every attributed sample of the **whole** launch (before the first
output file too): the members' shared MiB on the card (``per_pid_shared``), their dedicated MiB
(``per_pid``) and the card's ``used``. A sample that cannot be attributed, or has no shared figure for
the card, breaks the run. ``spill.outcome`` combines the render launches (a retry is a second launch):
spilled when any launch spilled, the largest figures; ``summary_values`` gives the five summary keys.
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import spill
from sethlans_worker_agent.unreal_executor.runner_types import VramTrace
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from tests.unit.worker.unreal._executor_fakes import phase_run

MINE, OTHER = 'pci:0000:41:00.0', 'pci:0000:0a:00.0'
ROOT, CHILD, STRANGER = 4242, 4343, 9999
OWN = frozenset({ROOT, CHILD})
REFERENCE, AT_GATE, MARGIN = 1000, 4000, 256
OVER = REFERENCE + MARGIN + 1
_UNSET = object()


def reading(taken_at, shared=OVER, dedicated=8000, used=12000, per_pid=_UNSET, per_pid_shared=_UNSET):
    """The launch's card alone (``phases`` cuts it); ``per_pid*`` keep every card of the machine."""
    if per_pid is _UNSET:
        per_pid = {MINE: {ROOT: dedicated, STRANGER: 3000}, OTHER: {CHILD: 50}}
    if per_pid_shared is _UNSET:
        per_pid_shared = {MINE: {ROOT: shared, STRANGER: 7000}, OTHER: {CHILD: 6000}}
    return GpuMemoryReading(taken_at, 'pdh', (GpuMem(1, 'Card', 24560, used, None, None, MINE),),
                            measures_card=True, per_pid=per_pid, per_pid_shared=per_pid_shared)


def watched(reference=REFERENCE, used_at_gate=AT_GATE):
    w = spill.SpillWatch(reference_mb=reference, used_at_gate_mb=used_at_gate, margin_mb=MARGIN, job_id=42)
    return VramTrace(spill=w), w


class TestTraceFeedsTheWatch:

    def test_the_members_shared_on_their_card_only(self):
        """Other processes on the card and the members' shared on another card are not the job's."""
        trace, w = watched()
        trace.add(reading(1.0, shared=700), OWN)
        assert (w.samples, w.shared_peak_mb) == (1, 700)

    def test_every_member_counts(self):
        trace, w = watched()
        trace.add(reading(1.0, per_pid_shared={MINE: {ROOT: 600, CHILD: 700}}), OWN)
        assert w.shared_peak_mb == 1300

    def test_the_start_up_spike_before_the_first_output_file_spills(self):
        trace, w = watched()
        trace.add(reading(1.0), OWN, window=False)
        trace.add(reading(2.0), OWN, window=False)
        for n in (3.0, 4.0, 5.0):
            trace.add(reading(n, shared=100), OWN, window=True)
        assert (w.spilled, w.cause, w.shared_peak_mb) == (True, spill.SELF, OVER)
        assert trace.job_peak_mb == 8000     # the dedicated peak still counts from the first file on

    def test_others_are_card_used_minus_the_members_dedicated(self):
        trace, w = watched()
        squeezed = 8000 + AT_GATE + MARGIN + 1
        trace.add(reading(1.0, used=squeezed), OWN)
        trace.add(reading(2.0, used=squeezed), OWN)
        assert (w.cause, w.others_peak_mb) == (spill.CONTENTION, AT_GATE + MARGIN + 1)

    def test_a_padded_key_in_the_shared_map_is_the_same_card(self):
        trace, w = watched()
        trace.add(reading(1.0, per_pid_shared={'pci:00000000:41:00.0': {ROOT: 900}}), OWN)
        assert w.shared_peak_mb == 900

    def test_members_absent_from_the_shared_map_count_zero(self):
        trace, w = watched()
        trace.add(reading(1.0, per_pid_shared={MINE: {STRANGER: 9000}}), OWN)
        assert (w.samples, w.shared_peak_mb) == (1, 0)

    @pytest.mark.parametrize('gap', [
        {'per_pid': None},                              # the process array could not be read
        {'per_pid': {OTHER: {ROOT: 50}}},              # the per-pid map lacks the job's card
        {'per_pid_shared': None},                       # no shared counter on this sample
        {'per_pid_shared': {OTHER: {ROOT: 9000}}},     # the shared map lacks the job's card
    ])
    def test_an_unattributable_sample_breaks_the_run(self, gap):
        trace, w = watched()
        trace.add(reading(1.0), OWN)
        trace.add(reading(2.0, **gap), OWN)
        trace.add(reading(3.0), OWN)
        assert w.spilled is False

    def test_a_missing_reading_breaks_the_run(self):
        """``None``: the sampler gave nothing, or ``phases`` cut a reading that lacked the job's card."""
        trace, w = watched()
        trace.add(reading(1.0), OWN)
        trace.add(None, OWN)
        trace.add(reading(2.0), OWN)
        assert (w.samples, w.spilled) == (2, False)

    def test_a_tick_without_own_pids_breaks_the_run(self):
        trace, w = watched()
        trace.add(reading(1.0), OWN)
        trace.add(reading(2.0), None)
        trace.add(reading(3.0), OWN)
        assert (w.samples, w.spilled) == (2, False)

    def test_a_repeated_reading_does_not_advance_the_run(self):
        trace, w = watched()
        same = reading(1.0)
        trace.add(same, OWN)
        trace.add(same, OWN)
        assert (w.samples, w.run, w.spilled) == (1, 1, False)

    def test_a_repeated_reading_does_not_break_the_run(self):
        trace, w = watched()
        first = reading(1.0)
        trace.add(first, OWN)
        trace.add(first, OWN)
        trace.add(reading(2.0), OWN)
        assert (w.samples, w.spilled) == (2, True)

    def test_no_shared_counter_all_launch_is_unknown(self):
        """Linux, or a Windows build without the counter: nothing is marked without evidence (NF-2)."""
        trace, w = watched()
        for n in (1.0, 2.0, 3.0):
            trace.add(reading(n, per_pid_shared=None), OWN)
        assert (w.spilled, w.cause, w.shared_peak_mb) == (None, None, None)

    def test_without_own_pids_the_watch_gets_nothing(self):
        """No Job Object: no per-job attribution at all."""
        trace, w = watched()
        trace.add(reading(1.0), None)
        trace.add(reading(2.0), None)
        assert (w.samples, w.spilled, w.others_peak_mb) == (0, None, None)

    def test_a_trace_without_a_watch_is_unchanged(self):
        trace = VramTrace()
        trace.add(reading(1.0), OWN, window=True)
        assert trace.spill is None and trace.job_peak_mb == 8000


def render(*shared, reference=REFERENCE, used=12000, phase='render'):
    """A launch whose watch saw ``shared`` (one sample each)."""
    trace, _w = watched(reference=reference)
    for n, value in enumerate(shared, start=1):
        trace.add(reading(float(n), shared=value, used=used), OWN)
    return phase_run(phase, vram=trace)


SQUEEZED = 8000 + AT_GATE + MARGIN + 1


class TestOutcome:

    def test_a_retry_spilled_if_any_launch_spilled(self):
        out = spill.outcome([render(OVER, OVER), render(100, 200)])
        assert (out.spilled, out.cause) == (True, spill.SELF)

    def test_the_second_launch_spilling_counts_too(self):
        assert spill.outcome([render(100), render(OVER, OVER + 40)]).spilled is True

    def test_the_largest_figures_over_the_launches(self):
        out = spill.outcome([render(OVER + 300, 10, used=11000), render(OVER, OVER, used=SQUEEZED)])
        assert (out.shared_peak_mb, out.others_peak_mb, out.reference_mb) == (OVER + 300, SQUEEZED - 8000, REFERENCE)
        assert out.cause == spill.CONTENTION

    def test_contention_when_any_spilled_launch_was(self):
        runs = [render(OVER, OVER), render(OVER, OVER, used=SQUEEZED)]
        assert spill.outcome(runs).cause == spill.CONTENTION

    def test_a_squeezed_launch_that_did_not_spill_gives_no_cause(self):
        runs = [render(OVER, 10, used=SQUEEZED), render(OVER, OVER)]
        assert spill.outcome(runs).cause == spill.SELF

    def test_no_and_unknown_is_no(self):
        assert spill.outcome([_unknown(), render(10)]).spilled is False

    def test_only_unknown_launches_are_unknown(self):
        """Others still come from the attributed samples (the dedicated split needs no shared counter)."""
        out = spill.outcome([_unknown(), _unknown()])
        assert out == spill.Spill(spilled=None, cause=None, shared_peak_mb=None, reference_mb=REFERENCE,
                                  others_peak_mb=4000)

    def test_warm_ups_and_launches_without_a_watch_are_not_counted(self):
        runs = [render(OVER, OVER, phase='warmup'), phase_run('render'), render(10)]
        out = spill.outcome(runs)
        assert (out.spilled, out.shared_peak_mb) == (False, 10)

    def test_no_render_launch_is_all_unknown(self):
        assert spill.outcome([]) == spill.Spill()
        assert spill.outcome([phase_run('prep')]) == spill.Spill()

    def test_shared_peak_is_the_last_render_launchs_largest(self):
        """A pilot's ``shared_peak_mb``: the launch whose figures it reports; a cut-short first launch is not."""
        runs = [render(2000, 700, reference=None), render(900, 20, reference=None), render(5000, phase='warmup')]
        assert spill.shared_peak(runs) == 900

    def test_shared_peak_without_a_watch_on_the_last_render_is_none(self):
        assert spill.shared_peak([render(2000, reference=None), phase_run('render')]) is None
        assert spill.shared_peak([phase_run('prep')]) is None


def _unknown():
    trace, _w = watched()
    trace.add(reading(1.0, per_pid_shared=None), OWN)
    return phase_run('render', vram=trace)


class TestSummaryValues:
    KEYS = ['spilled', 'spill_cause', 'spill_shared_peak_mb', 'spill_reference_mb', 'spill_others_peak_mb']

    def test_a_spill(self):
        values = spill.summary_values([render(OVER, OVER)])
        assert list(values) == self.KEYS
        assert values == {'spilled': 1, 'spill_cause': 'self', 'spill_shared_peak_mb': OVER,
                          'spill_reference_mb': REFERENCE, 'spill_others_peak_mb': 4000}

    def test_no_spill(self):
        values = spill.summary_values([render(10, 20, used=SQUEEZED)])
        assert (values['spilled'], values['spill_cause'], values['spill_others_peak_mb']) == (0, '', SQUEEZED - 8000)

    def test_unknown_is_empty(self):
        assert spill.summary_values([]) == dict.fromkeys(self.KEYS, '')

    def test_a_contention_spill(self):
        assert spill.summary_values([render(OVER, OVER, used=SQUEEZED)])['spill_cause'] == 'contention'

    def test_render_watches_lists_render_launches_in_order(self):
        first, second = render(1), render(2)
        runs = [first, render(3, phase='warmup'), SimpleNamespace(phase='render', outcome=SimpleNamespace(vram=None)),
                second]
        assert spill.render_watches(runs) == [first.outcome.vram.spill, second.outcome.vram.spill]
