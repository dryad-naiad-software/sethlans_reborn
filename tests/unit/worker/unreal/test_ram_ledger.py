# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The RAM ledger (render per card FR-PC-RAM-5, D9's outstanding figures, D16 per-claim keys)."""
import threading

import pytest

from sethlans_worker_agent.unreal_executor.ram_ledger import (
    BLENDER_WINDOW_SECONDS, STALE_SECONDS, UNAVAILABLE_REASON, Outstanding, RamLedger)


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ledger(clock):
    return RamLedger(clock=clock)


class TestUnrealClaims:

    def test_an_unlaunched_claim_counts_its_whole_need(self, ledger):
        ledger.register('a', 9000, 30000)
        assert ledger.outstanding(blender_mb=4096) == Outstanding(9000, 30000)

    def test_published_figures_reduce_the_outstanding_part(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.launch_started('a')
        assert ledger.publish('a', 6000, 31000) is True
        assert ledger.outstanding(blender_mb=4096) == Outstanding(3000, 0)

    def test_the_asking_claim_is_left_out(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.register('b', 1000, 2000)
        assert ledger.outstanding(blender_mb=0, exclude='b') == Outstanding(9000, 30000)

    def test_a_launch_end_returns_the_current_figures_to_zero(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.launch_started('a')
        ledger.publish('a', 9000, 30000)
        ledger.launch_ended('a')
        entry = ledger.snapshot()['a']
        assert (entry.current_ws, entry.current_commit, entry.in_launch) == (0, 0, False)
        assert ledger.outstanding(blender_mb=0) == Outstanding(9000, 30000)

    def test_a_claim_is_registered_once(self, ledger):
        ledger.register('a', 1, 1)
        with pytest.raises(ValueError, match='already'):
            ledger.register('a', 1, 1)

    @pytest.mark.parametrize('bad', [-1, 1.5, True, None, '10'])
    def test_figures_must_be_non_negative_integers(self, ledger, bad):
        with pytest.raises(ValueError):
            ledger.register('a', bad, 1)
        ledger.register('b', 1, 1)
        with pytest.raises(ValueError):
            ledger.publish('b', 1, bad)

    def test_unregister_removes_the_claim_and_late_ticks_are_ignored(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.unregister('a')
        ledger.unregister('a')
        assert ledger.publish('a', 1, 1) is False
        assert ledger.mark_failed('a') is False
        assert ledger.launch_started('a') is False
        assert ledger.snapshot() == {} and not ledger.has_claims()
        assert ledger.outstanding(blender_mb=4096) == Outstanding(0, 0)


class TestFailsClosed:

    def test_a_failed_entry_fails_closed(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.launch_started('a')
        ledger.mark_failed('a')
        assert ledger.outstanding(blender_mb=0).reason == UNAVAILABLE_REASON

    def test_a_good_publish_clears_failed(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.launch_started('a')
        ledger.mark_failed('a')
        ledger.publish('a', 9000, 30000)
        assert ledger.outstanding(blender_mb=0) == Outstanding(0, 0)

    def test_a_read_that_failed_as_the_tree_exited_clears_at_the_launch_end(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.launch_started('a')
        ledger.mark_failed('a')   # e.g. error 87: the process exited between pids() and OpenProcess
        ledger.launch_ended('a')
        assert ledger.snapshot()['a'].failed is False
        assert ledger.outstanding(blender_mb=0) == Outstanding(9000, 30000)

    def test_the_next_launch_starts_with_failed_cleared(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.mark_failed('a')
        ledger.launch_started('a')
        assert ledger.snapshot()['a'].failed is False
        assert ledger.outstanding(blender_mb=0) == Outstanding(9000, 30000)

    def test_publish_stamps_the_ledgers_clock(self, ledger, clock):
        ledger.register('a', 1, 1)
        clock.now = 1234.5
        ledger.publish('a', 1, 1)
        assert ledger.snapshot()['a'].published_at == 1234.5

    def test_publish_takes_no_caller_time(self, ledger):
        ledger.register('a', 1, 1)
        with pytest.raises(TypeError):
            ledger.publish('a', 1, 1, at=0.0)

    def test_a_stale_figure_during_a_launch_fails_closed(self, ledger, clock):
        ledger.register('a', 9000, 30000)
        ledger.launch_started('a')
        ledger.publish('a', 100, 100)
        clock.now += STALE_SECONDS
        assert ledger.outstanding(blender_mb=0).reason == ''
        clock.now += 0.5
        assert ledger.outstanding(blender_mb=0).reason == UNAVAILABLE_REASON

    def test_a_launch_that_never_published_goes_stale_from_its_start(self, ledger, clock):
        ledger.register('a', 9000, 30000)
        ledger.launch_started('a')
        assert ledger.outstanding(blender_mb=0) == Outstanding(9000, 30000)
        clock.now += STALE_SECONDS + 1
        assert ledger.outstanding(blender_mb=0).reason == UNAVAILABLE_REASON

    def test_an_old_figure_between_launches_is_not_stale(self, ledger, clock):
        ledger.register('a', 9000, 30000)
        ledger.launch_started('a')
        ledger.publish('a', 100, 100)
        ledger.launch_ended('a')
        clock.now += 600
        assert ledger.outstanding(blender_mb=0) == Outstanding(9000, 30000)

    def test_the_asking_claims_own_failure_does_not_count(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.mark_failed('a')
        assert ledger.outstanding(blender_mb=0, exclude='a') == Outstanding(0, 0)


class TestBlender:

    def test_a_recent_blender_claim_counts_for_both_kinds(self, ledger, clock):
        ledger.register_blender(7, at=clock.now - 10)
        assert ledger.has_claims()
        assert ledger.outstanding(blender_mb=4096) == Outstanding(4096, 4096)

    def test_a_blender_claim_counts_only_inside_its_window(self, ledger, clock):
        ledger.register_blender(7)
        clock.now += BLENDER_WINDOW_SECONDS
        assert ledger.outstanding(blender_mb=4096) == Outstanding(4096, 4096)
        clock.now += 1
        assert ledger.outstanding(blender_mb=4096) == Outstanding(0, 0)

    def test_released_blender_claims_are_dropped(self, ledger):
        ledger.register_blender(7)
        ledger.register_blender(8)
        ledger.unregister_blender(7)
        ledger.unregister_blender(7)
        assert ledger.outstanding(blender_mb=1000) == Outstanding(1000, 1000)

    def test_unreal_and_blender_add_up(self, ledger):
        ledger.register('a', 9000, 30000)
        ledger.register_blender(7)
        assert ledger.outstanding(blender_mb=4096) == Outstanding(13096, 34096)


def test_runner_threads_publish_while_the_loop_reads(ledger):
    """Publishes from job threads and reads from the agent loop never corrupt the ledger."""
    ids = [f'claim-{n}' for n in range(4)]
    for claim_id in ids:
        ledger.register(claim_id, 1000, 2000)
        ledger.launch_started(claim_id)
    stop = threading.Event()

    def runner(claim_id):
        value = 0
        while not stop.is_set():
            value = (value + 1) % 1000
            ledger.publish(claim_id, value, value)

    threads = [threading.Thread(target=runner, args=(claim_id,)) for claim_id in ids]
    for thread in threads:
        thread.start()
    try:
        for _ in range(2000):
            result = ledger.outstanding(blender_mb=0)
            assert result.reason == '' and 0 < result.ws <= 4000 and 4000 <= result.commit <= 8000
    finally:
        stop.set()
        for thread in threads:
            thread.join()
