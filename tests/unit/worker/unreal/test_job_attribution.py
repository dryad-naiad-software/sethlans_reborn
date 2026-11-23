# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The job's own card memory (render per card FR-PC-ATT-2): each sample records the launch's Job Object
members' memory on its card and the largest on any other card, from the reading's per-pid map; a
render counts from its first output file on (``output_watch``), and the runner hands its members'
pids to the trace on every tick.
"""
import os

from sethlans_worker_agent.unreal_executor.output_watch import FirstOutput
from sethlans_worker_agent.unreal_executor.runner_types import VramTrace
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from tests.unit.worker.unreal._process_runner_world import World

from ._runner_fakes import ROOT_PID

MINE, OTHER, THIRD = 'pci:0000:41:00.0', 'pci:0000:0a:00.0', 'pci:0000:0b:00.0'
OWN = frozenset({ROOT_PID, 1001})


def reading(taken_at, per_pid, key=MINE, used=9000):
    """The launch's card alone (``phases`` cuts it); ``per_pid`` keeps every card of the machine."""
    return GpuMemoryReading(taken_at, 'pdh', (GpuMem(1, 'Card', 24560, used, None, None, key),), measures_card=True,
                            per_pid=per_pid)


class TestTrace:

    def test_own_members_on_its_card_and_the_largest_other_card(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 7000, 1001: 300, 9999: 5000}, OTHER: {ROOT_PID: 40},
                                THIRD: {1001: 90, 9999: 3000}}), OWN, window=True)
        assert trace.job_peak_mb == 7300 and trace.job_other_cards_mb == 90 and not trace.job_unknown
        assert trace.max_job == {1: 7300}

    def test_the_peak_is_the_largest_sample(self):
        trace = VramTrace()
        for n, used in enumerate((6000, 8100, 7000), start=1):
            trace.add(reading(float(n), {MINE: {ROOT_PID: used}, OTHER: {}}), OWN, window=True)
        assert trace.job_peak_mb == 8100 and trace.job_other_cards_mb == 0

    def test_before_the_first_output_only_the_pilot_figure_counts(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 9500}, OTHER: {ROOT_PID: 800}}), OWN, window=False)
        trace.add(reading(2.0, {MINE: {ROOT_PID: 7000}, OTHER: {}}), OWN, window=True)
        assert trace.max_job == {1: 9500}                              # the pilot's process peak: every sample
        assert (trace.job_peak_mb, trace.job_other_cards_mb) == (7000, 0)  # the summary: from the first file on

    def test_a_machine_with_one_card_has_no_other_card_memory(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 7000}}), OWN, window=True)
        assert trace.job_other_cards_mb == 0

    def test_a_sample_without_a_per_pid_map_makes_the_window_unknown(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 7000}}), OWN, window=True)
        trace.add(reading(2.0, None), OWN, window=True)    # e.g. the process array could not be read
        assert trace.job_unknown and trace.max_job == {1: 7000}

    def test_a_map_without_the_jobs_card_is_not_attributed(self):
        trace = VramTrace()
        trace.add(reading(1.0, {OTHER: {ROOT_PID: 40}}), OWN, window=True)
        assert trace.job_unknown and trace.job_peak_mb is None and trace.max_job == {}

    def test_an_unattributed_sample_before_the_window_does_not_count(self):
        trace = VramTrace()
        trace.add(reading(1.0, None), OWN, window=False)
        trace.add(reading(2.0, {MINE: {ROOT_PID: 7000}}), OWN, window=True)
        assert not trace.job_unknown and trace.job_peak_mb == 7000

    def test_without_own_pids_nothing_is_attributed(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 7000}}))
        assert trace.max_job == {} and trace.job_peak_mb is None and not trace.job_unknown

    def test_a_padded_key_spelling_is_the_same_card(self):
        trace = VramTrace()
        trace.add(reading(1.0, {'pci:00000000:41:00.0': {ROOT_PID: 7000}}), OWN, window=True)
        assert trace.job_peak_mb == 7000


class TestOthers:
    """Q10: other programs' use of the launch's card is its ``used`` minus the members' own MiB on it."""

    def test_card_used_minus_the_members_on_that_card(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 7000, 1001: 300, 9999: 5000}, OTHER: {ROOT_PID: 40}}), OWN)
        assert trace.max_others == {1: 9000 - 7300}

    def test_the_largest_over_every_attributed_sample_start_up_included(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 7000}}, used=9000), OWN, window=False)   # 2000, before any file
        trace.add(reading(2.0, {MINE: {ROOT_PID: 8000}}, used=9500), OWN, window=True)    # 1500
        trace.add(reading(3.0, None, used=20000), OWN, window=True)                       # not split by process
        assert trace.max_others == {1: 2000}

    def test_members_above_the_card_figure_floor_at_zero(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 9500}}, used=9000), OWN)
        assert trace.max_others == {1: 0}

    def test_a_padded_key_spelling_is_the_same_card(self):
        trace = VramTrace()
        trace.add(reading(1.0, {'pci:00000000:41:00.0': {ROOT_PID: 6000}}), OWN)
        assert trace.max_others == {1: 3000}

    def test_others_at_the_card_peak_sample(self):
        """Q10 band 2: what a card peak already holds of other programs' use (``pilot_others.counted``)."""
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 7000}}, used=9000), OWN)    # others 2000
        trace.add(reading(2.0, {MINE: {ROOT_PID: 8000}}, used=9500), OWN)    # the peak: others 1500
        trace.add(reading(3.0, {MINE: {ROOT_PID: 4000}}, used=9200), OWN)    # others 5200, not the peak
        assert trace.others_at_max == {1: 1500} and trace.max_others == {1: 5200}

    def test_a_new_peak_that_cannot_be_split_drops_the_older_figure(self):
        trace = VramTrace()
        trace.add(reading(1.0, {MINE: {ROOT_PID: 7000}}, used=9000), OWN)
        trace.add(reading(2.0, None, used=20000), OWN)
        assert trace.others_at_max == {} and trace.max_used == {1: 20000}

    def test_a_tie_with_the_peak_keeps_the_smaller_figure(self):
        for owns in ((7000, 8000), (8000, 7000)):
            trace = VramTrace()
            for n, own in enumerate(owns, start=1):
                trace.add(reading(float(n), {MINE: {ROOT_PID: own}}, used=9500), OWN)
            assert trace.others_at_max == {1: 1500}

    def test_a_sample_that_cannot_be_split_by_process_fills_nothing(self):
        for per_pid, own in ((None, OWN), ({OTHER: {ROOT_PID: 40}}, OWN), ({MINE: {ROOT_PID: 7000}}, None)):
            trace = VramTrace()
            trace.add(reading(1.0, per_pid), own, window=True)
            assert trace.max_others == {} and trace.samples == 1


class TestFirstOutput:

    def test_seen_once_a_file_is_in_the_folder(self, tmp_path):
        watch = FirstOutput(tmp_path)
        assert watch.seen(0.0) is False
        (tmp_path / 'sethlans_42.1000.png').write_bytes(b'x')
        assert watch.seen(1.0) is False        # checked at most every 2 s
        assert watch.seen(2.0) is True
        os.remove(tmp_path / 'sethlans_42.1000.png')
        assert watch.seen(2.5) is True         # latched for the launch

    def test_a_subfolder_is_not_a_file(self, tmp_path):
        (tmp_path / 'sub').mkdir()
        assert FirstOutput(tmp_path).seen(0.0) is False

    def test_no_folder_is_never_seen(self, tmp_path):
        assert FirstOutput(None).seen(0.0) is False
        assert FirstOutput(tmp_path / 'missing').seen(0.0) is False


class TestRunnerHandsItsMembers:

    def test_the_render_counts_from_its_first_output_file(self, tmp_path):
        world = World(tmp_path)
        out = tmp_path / 'output'
        out.mkdir()

        def sample(timeout_s):
            return reading(world.clock.now, {MINE: {ROOT_PID: 6000 if (out / 'f.png').exists() else 9000}})
        world.steps[3] = lambda: (out / 'f.png').write_bytes(b'x')
        world.steps[8] = lambda: setattr(world.root, 'alive', False)
        outcome = world.run(world.spec(vram_sample=sample, first_output_dir=out))
        assert outcome.vram.max_job == {1: 9000} and outcome.vram.job_peak_mb == 6000

    def test_without_an_output_folder_there_is_no_window(self, tmp_path):
        world = World(tmp_path)
        world.steps[3] = lambda: setattr(world.root, 'alive', False)
        outcome = world.run(world.spec(vram_sample=lambda t: reading(world.clock.now, {MINE: {ROOT_PID: 5000}})))
        assert outcome.vram.max_job == {1: 5000} and outcome.vram.job_peak_mb is None
        assert outcome.vram.max_others == {1: 9000 - 5000}   # Q10: every attributed sample, no window needed
