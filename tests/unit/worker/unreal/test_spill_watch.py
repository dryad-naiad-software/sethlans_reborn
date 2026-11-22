# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``spill.SpillWatch`` over one render launch (Unreal per-machine memory test D9, FR-MT-S-2).

A sample is **over** when the job's own shared usage on its card is above ``reference + margin``
(exactly at that line is not over); the launch **spilled** after 2 consecutive over samples. Any sample
that is not over, or carries no shared figure, breaks the run. **Cause** at an over sample:
``others = card used - own dedicated``; ``others > used_at_gate + margin`` is contention. A spill is
``contention`` when any over sample of a run that reached 2 was (the developer's rule), else ``self``.
``spilled`` is None without a reference, without ``used_at_gate`` or without any shared figure.
"""
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import spill

REFERENCE, AT_GATE, MARGIN = 1000, 4000, 256
LINE = REFERENCE + MARGIN          # 1256: at the line is not over
OVER = LINE + 1
OWN_DEDICATED = 8000
CALM = OWN_DEDICATED + AT_GATE + MARGIN          # others == used_at_gate + margin: not contention
SQUEEZED = CALM + 1                               # others one past the line: contention


@pytest.fixture(autouse=True)
def _margin(monkeypatch):
    """The margin is read when a watch is made; a developer's config.ini or env must not change these tests."""
    monkeypatch.setattr(config_unreal, 'SPILL_MARGIN_MB', MARGIN)


def watch(reference=REFERENCE, used_at_gate=AT_GATE, margin=MARGIN):
    return spill.SpillWatch(reference_mb=reference, used_at_gate_mb=used_at_gate, margin_mb=margin, job_id=42)


def feed(w, *shared, card_used=CALM):
    for value in shared:
        w.observe(value, OWN_DEDICATED, card_used)
    return w


class TestConsecutive:

    def test_one_over_sample_is_not_a_spill(self):
        w = feed(watch(), 100, OVER, 100)
        assert (w.spilled, w.cause) == (False, None)

    def test_two_consecutive_over_samples_spill(self):
        w = feed(watch(), 100, OVER, OVER)
        assert (w.spilled, w.cause) == (True, spill.SELF)

    def test_a_sample_under_the_line_breaks_the_run(self):
        assert feed(watch(), OVER, 100, OVER, 100).spilled is False

    def test_exactly_at_reference_plus_margin_is_not_over(self):
        assert feed(watch(), LINE, LINE, LINE).spilled is False

    def test_one_mb_past_the_line_is_over(self):
        assert feed(watch(), LINE + 1, LINE + 1).spilled is True

    def test_the_margin_is_part_of_the_line(self):
        assert feed(watch(margin=0), REFERENCE + 1, REFERENCE + 1).spilled is True
        assert feed(watch(margin=0), REFERENCE, REFERENCE).spilled is False

    def test_a_sample_without_a_shared_figure_breaks_the_run(self):
        w = feed(watch(), OVER, None, OVER)
        assert w.spilled is False and w.samples == 2

    def test_missing_attribution_breaks_the_run(self):
        w = watch()
        w.observe(OVER, OWN_DEDICATED, CALM)
        w.missing()
        w.observe(OVER, OWN_DEDICATED, CALM)
        assert w.spilled is False

    def test_a_spill_stays_spilled_when_the_usage_falls_back(self):
        w = feed(watch(), OVER, OVER, 100, 100, 100)
        assert (w.spilled, w.cause) == (True, spill.SELF)


class TestCause:

    def test_others_past_used_at_gate_plus_margin_is_contention(self):
        w = feed(watch(), OVER, OVER, card_used=SQUEEZED)
        assert (w.spilled, w.cause) == (True, spill.CONTENTION)

    def test_others_exactly_at_the_line_is_self(self):
        assert feed(watch(), OVER, OVER, card_used=CALM).cause == spill.SELF

    @pytest.mark.parametrize('first,second', [(SQUEEZED, CALM), (CALM, SQUEEZED)])
    def test_mixed_causes_in_a_qualifying_run_are_contention(self, first, second):
        w = watch()
        w.observe(OVER, OWN_DEDICATED, first)
        w.observe(OVER, OWN_DEDICATED, second)
        assert (w.spilled, w.cause) == (True, spill.CONTENTION)

    def test_a_squeezed_sample_later_in_a_spilled_run_makes_it_contention(self):
        w = feed(watch(), OVER, OVER, card_used=CALM)
        w.observe(OVER, OWN_DEDICATED, SQUEEZED)
        assert w.cause == spill.CONTENTION

    def test_contention_of_a_run_that_never_reached_two_does_not_count(self):
        w = watch()
        w.observe(OVER, OWN_DEDICATED, SQUEEZED)     # a lone over sample, squeezed
        w.observe(100, OWN_DEDICATED, CALM)          # breaks the run
        feed(w, OVER, OVER, card_used=CALM)          # a later run of the job's own
        assert (w.spilled, w.cause) == (True, spill.SELF)

    def test_a_squeezed_sample_under_the_line_is_no_cause(self):
        w = watch()
        w.observe(100, OWN_DEDICATED, SQUEEZED)
        feed(w, OVER, OVER, card_used=CALM)
        assert w.cause == spill.SELF

    def test_own_dedicated_above_the_card_figure_floors_others_at_zero(self):
        w = watch()
        w.observe(OVER, OWN_DEDICATED, OWN_DEDICATED - 500)
        assert w.others_peak_mb == 0


class TestUnknown:

    def test_no_reference_is_unknown_but_the_shared_peak_is_kept(self):
        """A pilot (or a result whose ``shared_peak_mb`` is null): its own figure is the jobs' reference."""
        w = feed(watch(reference=None), 900, 5000, 5000)
        assert (w.spilled, w.cause, w.shared_peak_mb, w.active) == (None, None, 5000, False)

    def test_no_used_at_gate_is_unknown(self):
        w = feed(watch(used_at_gate=None), OVER, OVER)
        assert (w.spilled, w.cause) == (None, None)

    def test_no_sample_with_a_shared_figure_is_unknown(self):
        w = watch()
        w.missing()
        w.observe(None, OWN_DEDICATED, CALM)
        assert (w.spilled, w.cause, w.samples, w.shared_peak_mb) == (None, None, 0, None)
        assert w.others_peak_mb == AT_GATE + MARGIN   # others still count: the card was attributed

    def test_no_sample_at_all_is_unknown(self):
        w = watch()
        assert (w.spilled, w.cause, w.shared_peak_mb, w.others_peak_mb) == (None, None, None, None)

    def test_samples_without_a_spill_say_no(self):
        assert feed(watch(), 0, 300).spilled is False


class TestFigures:

    def test_the_largest_shared_and_others_are_kept(self):
        w = watch()
        w.observe(700, 8000, 9000)       # others 1000
        w.observe(1500, 6000, 9500)      # others 3500
        w.observe(400, 9000, 10000)      # others 1000
        assert (w.shared_peak_mb, w.others_peak_mb) == (1500, 3500)


class TestWarning:

    def test_one_warning_the_first_time_a_launch_spills(self, caplog):
        with caplog.at_level(logging.WARNING, logger=spill.__name__):
            feed(watch(), OVER, OVER, OVER + 50, 100, OVER, OVER)
        lines = [r.getMessage() for r in caplog.records if r.name == spill.__name__]
        assert len(lines) == 1
        assert lines[0].startswith('Unreal job 42 spills into shared memory') and 'the job runs on' in lines[0]
        assert f'{OVER} MB' in lines[0] and f'{REFERENCE} MB + {MARGIN} MB margin' in lines[0]

    def test_no_warning_without_a_spill(self, caplog):
        with caplog.at_level(logging.WARNING, logger=spill.__name__):
            feed(watch(), OVER, 100, OVER)
            feed(watch(reference=None), 9000, 9000)
        assert [r for r in caplog.records if r.name == spill.__name__] == []

    def test_each_launch_warns_on_its_own_watch(self, caplog):
        with caplog.at_level(logging.WARNING, logger=spill.__name__):
            feed(watch(), OVER, OVER)
            feed(watch(), OVER, OVER)
        assert len([r for r in caplog.records if r.name == spill.__name__]) == 2


class TestWatchFor:

    def _gate(self, shared_peak_mb=512, used_mb=3000):
        return SimpleNamespace(result=SimpleNamespace(shared_peak_mb=shared_peak_mb), used_mb=used_mb)

    def test_a_watch_from_the_gates_result_and_used(self):
        w = spill.watch_for(self._gate(), 7)()
        assert (w.reference_mb, w.used_at_gate_mb, w.margin_mb, w.job_id) == (512, 3000, MARGIN, 7)
        assert (w.samples, w.run, w.spilled_seen) == (0, 0, False)

    def test_each_call_is_a_new_watch(self):
        factory = spill.watch_for(self._gate(), 7)
        first, second = factory(), factory()
        first.observe(9000, 1, 2)
        assert first is not second and second.samples == 0

    def test_the_margin_is_read_when_the_factory_is_made(self, monkeypatch):
        monkeypatch.setattr(config_unreal, 'SPILL_MARGIN_MB', 640)
        factory = spill.watch_for(self._gate(), 7)
        monkeypatch.setattr(config_unreal, 'SPILL_MARGIN_MB', 1)
        assert factory().margin_mb == 640

    @pytest.mark.parametrize('gate', [
        SimpleNamespace(result=None, used_mb=3000),               # a pilot: no result
        SimpleNamespace(result=SimpleNamespace(shared_peak_mb=None), used_mb=3000),
        SimpleNamespace(),                                         # a gate without the fields
    ])
    def test_no_reference_makes_an_inactive_watch(self, gate):
        w = spill.watch_for(gate, 7)()
        assert w.reference_mb is None and w.active is False

    def test_no_used_at_gate_makes_an_inactive_watch(self):
        w = spill.watch_for(self._gate(used_mb=None), 7)()
        assert w.used_at_gate_mb is None and w.active is False
