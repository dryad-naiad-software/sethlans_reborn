# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Spill-over FR-MT-S-1 (per machine memory test D9): the Windows ``pdh`` backend's ``per_pid_shared`` from the
``\\GPU Process Memory(*)\\Shared Usage`` items, and ``open()`` falling back to the dedicated counters when the
shared counter cannot be added. Fake queries and PDH items only; no DLL is loaded, so these run on every OS.
"""
import ctypes
import logging

import pytest

from sethlans_worker_agent.gpu_memory import win_backend, win_pdh
from sethlans_worker_agent.gpu_memory.win_pdh import PDH_CSTATUS_NEW_DATA, PDH_CSTATUS_NO_INSTANCE, PdhError
from tests.unit.worker.gpu_memory._win_fakes import (
    ADAPTER, KEY_A, KEY_B, LUID_A, LUID_B, MIB, OTHER_PID, OURS, PROCESS, FakeApi, FakeQuery, adapter_item, hex_luid,
    make_backend, os_card, process_item)
from tests.unit.worker.gpu_memory.test_win_pdh import FakeArray

LOG = 'sethlans_worker_agent.gpu_memory.win_backend'
SHARED = win_backend.SHARED_PATH
ADAPTERS = [adapter_item(LUID_A, 3000), adapter_item(LUID_B, 1000)]
MISSING = object()
SHARED_LINE = ("The Windows GPU counters could not be opened with the process Shared Usage counter; "
               "opened without it, so spill-over is not measured: ")
OPEN_LINE = "The Windows GPU memory counters cannot be opened: "
OPEN_WORDS = ' be opened'   # in both lines: the fallback ("could not be opened") and the failure ("cannot be opened")


def three(processes=(), shared=(), adapters=ADAPTERS):
    """A collect over the three counters; ``None`` is a failed array, ``MISSING`` leaves the shared key out."""
    data = {ADAPTER: list(adapters), PROCESS: None if processes is None else list(processes)}
    if shared is not MISSING:
        data[SHARED] = None if shared is None else list(shared)
    return data


def sampled(processes=(), shared=(), pids=OURS):
    backend = make_backend(FakeQuery([three(processes, shared)]))
    assert backend.open() is True
    return backend.sample(pids)


class TestSharedFromItems:

    def test_the_paths_are_the_dedicated_ones_plus_shared_usage(self):
        assert SHARED == r'\GPU Process Memory(*)\Shared Usage'
        assert win_backend.DEDICATED_PATHS == (ADAPTER, PROCESS)
        assert win_backend.PATHS == (ADAPTER, PROCESS, SHARED)

    def test_each_pid_on_each_card_in_mib_by_card_key(self):
        reading = sampled(shared=[process_item(4242, LUID_A, 700), process_item(4343, LUID_A, 30),
                                  process_item(4343, LUID_B, 12), process_item(OTHER_PID, LUID_A, 900)])
        assert reading.per_pid_shared == {KEY_A: {4242: 700, 4343: 30}, KEY_B: {4343: 12}}

    def test_the_same_items_give_the_same_map_as_dedicated_usage(self):
        items = [process_item(4242, LUID_A, 1200), process_item(4343, LUID_B, 40, phys=1),
                 process_item(OTHER_PID, LUID_B, 5)]
        reading = sampled(processes=items, shared=items)
        assert reading.per_pid_shared == reading.per_pid == {KEY_A: {4242: 1200}, KEY_B: {4343: 40}}

    def test_shared_and_dedicated_are_read_from_their_own_arrays(self):
        reading = sampled(processes=[process_item(4242, LUID_A, 1500)], shared=[process_item(4242, LUID_A, 250)])
        assert reading.per_pid == {KEY_A: {4242: 1500}, KEY_B: {}}
        assert reading.per_pid_shared == {KEY_A: {4242: 250}, KEY_B: {}}

    def test_shared_usage_never_counts_toward_the_cards_attribution(self):
        reading = sampled(processes=[process_item(4242, LUID_A, 100)], shared=[process_item(4242, LUID_A, 2000)])
        assert [gpu.sethlans_used_mb for gpu in reading.gpus] == [100, 0]
        assert [gpu.used_mb for gpu in reading.gpus] == [3000, 1000]

    def test_a_pids_phys_items_are_summed_before_flooring(self):
        halves = [(process_item(4242, LUID_A, 0, phys=n)[0], MIB // 2, win_pdh.PDH_CSTATUS_VALID_DATA)
                  for n in (0, 1, 2)]
        assert sampled(shared=halves).per_pid_shared[KEY_A] == {4242: 1}   # 1.5 MiB floored once

    def test_bytes_below_one_mib_floor_to_zero_but_the_pid_is_listed(self):
        item = (process_item(4242, LUID_A, 0)[0], MIB - 1, win_pdh.PDH_CSTATUS_VALID_DATA)
        assert sampled(shared=[item]).per_pid_shared == {KEY_A: {4242: 0}, KEY_B: {}}

    def test_cards_none_of_the_pids_use_have_an_empty_map(self):
        assert sampled(shared=[process_item(OTHER_PID, LUID_A, 900)]).per_pid_shared == {KEY_A: {}, KEY_B: {}}

    def test_an_empty_shared_array_is_a_map_of_empty_cards_not_none(self):
        assert sampled(shared=[]).per_pid_shared == {KEY_A: {}, KEY_B: {}}

    def test_only_the_given_pids_are_kept(self):
        reading = sampled(shared=[process_item(4242, LUID_A, 100), process_item(4343, LUID_A, 200)],
                          pids=frozenset({4343}))
        assert reading.per_pid_shared == {KEY_A: {4343: 200}, KEY_B: {}}

    def test_no_pids_gives_empty_cards(self):
        reading = sampled(shared=[process_item(4242, LUID_A, 100)], pids=frozenset())
        assert reading.per_pid_shared == {KEY_A: {}, KEY_B: {}}

    def test_lower_case_hex_and_new_data_items_count(self):
        name = f"pid_4242_luid_{hex_luid(LUID_B, upper=False)}_phys_0"
        reading = sampled(shared=[(name, 64 * MIB, PDH_CSTATUS_NEW_DATA)])
        assert reading.per_pid_shared == {KEY_A: {}, KEY_B: {4242: 64}}

    @pytest.mark.parametrize('item', [
        process_item(4242, LUID_A, 100, status=PDH_CSTATUS_NO_INSTANCE),
        (process_item(4242, LUID_A, 0)[0], -MIB, win_pdh.PDH_CSTATUS_VALID_DATA),
        ('pid_x_luid_0x00000000_0x0001A2B3_phys_0', MIB, win_pdh.PDH_CSTATUS_VALID_DATA),
        ('luid_0x00000000_0x0001A2B3_phys_0', MIB, win_pdh.PDH_CSTATUS_VALID_DATA),
        ('', MIB, win_pdh.PDH_CSTATUS_VALID_DATA)], ids=['no-instance', 'negative', 'bad-pid', 'adapter-name', 'empty'])
    def test_invalid_items_are_skipped(self, item):
        assert sampled(shared=[item]).per_pid_shared == {KEY_A: {}, KEY_B: {}}

    def test_an_item_on_a_luid_of_no_kept_card_is_ignored(self):
        reading = sampled(shared=[process_item(4242, (0, 0x00ABCDEF), 500), process_item(4242, LUID_A, 8)])
        assert reading.per_pid_shared == {KEY_A: {4242: 8}, KEY_B: {}}


class TestSharedNotRead:

    def test_a_failed_shared_array_gives_none_and_keeps_per_pid(self):
        reading = sampled(processes=[process_item(4242, LUID_A, 300)], shared=None)
        assert reading.per_pid_shared is None
        assert reading.per_pid == {KEY_A: {4242: 300}, KEY_B: {}} and reading.measures_process is True

    def test_a_collect_without_the_shared_array_gives_none(self):
        assert sampled(processes=[process_item(4242, LUID_A, 300)], shared=MISSING).per_pid_shared is None

    def test_a_failed_dedicated_array_does_not_stop_the_shared_map(self):
        reading = sampled(processes=None, shared=[process_item(4343, LUID_B, 70)])
        assert reading.per_pid is None and reading.measures_process is False
        assert reading.per_pid_shared == {KEY_A: {}, KEY_B: {4343: 70}}

    def test_each_sample_reads_its_own_collect(self):
        query = FakeQuery([three(), three(shared=[process_item(4242, LUID_A, 9)]), three(shared=None),
                           three(shared=[process_item(4242, LUID_A, 11)])])
        backend = make_backend(query)
        assert backend.open() is True
        maps = [backend.sample(OURS).per_pid_shared for _ in range(3)]
        assert maps == [{KEY_A: {4242: 9}, KEY_B: {}}, None, {KEY_A: {4242: 11}, KEY_B: {}}]

    def test_the_cross_check_reading_has_no_shared_map(self):
        checks = []
        backend = make_backend(FakeQuery([three(shared=[process_item(4242, LUID_A, 9)])]), checks=checks)
        backend.open()
        assert backend.sample(OURS).per_pid_shared is not None
        backend.before_sample()
        assert len(checks) == 1 and checks[0].per_pid_shared is None and checks[0].per_pid is None


class TestBuildReading:

    CARDS = (win_backend.Card(os_card(0, KEY_A), 1), win_backend.Card(os_card(1, None, 'Test Card B'), 2))

    def test_shared_by_pid_fills_per_pid_shared_for_keyed_cards_only(self):
        reading = win_backend.build_reading(self.CARDS, {1: MIB, 2: MIB}, None, 1.0,
                                            shared_by_pid={1: {4242: 3 * MIB}, 2: {4242: 5 * MIB}})
        assert reading.per_pid_shared == {KEY_A: {4242: 3}}
        assert reading.per_pid is None

    def test_without_shared_by_pid_the_field_is_none(self):
        reading = win_backend.build_reading(self.CARDS, {1: MIB, 2: MIB}, {1: 0}, 1.0, {1: {}})
        assert reading.per_pid == {KEY_A: {}} and reading.per_pid_shared is None

    def test_a_partial_reading_is_none_whatever_the_shared_map(self):
        assert win_backend.build_reading(self.CARDS, {1: MIB}, None, 1.0, shared_by_pid={1: {}}) is None


class OpenedPaths(FakeQuery):
    """A ``FakeQuery`` whose collects hold only the paths it was opened with, as ``PdhQuery`` does."""

    def collect(self):
        data = super().collect()
        return None if data is None else {path: items for path, items in data.items() if path in self.paths}


def refusing(query, first_error, second_error=None):
    """A query factory that raises ``first_error`` for a path list with ``Shared Usage`` (and ``second_error``)."""
    def factory(paths):
        factory.calls.append(tuple(paths))
        if SHARED in paths:
            raise first_error
        if second_error is not None:
            raise second_error
        return query(paths)

    factory.calls = []
    return factory


OPEN_ERRORS = [PdhError('PdhAddEnglishCounterW failed'), OSError('refused'), TypeError('bad type'),
               ValueError('bad value'), AttributeError('no export'), ctypes.ArgumentError('argument 2: wrong type')]


class TestOpenRetry:

    @pytest.mark.parametrize('error', OPEN_ERRORS, ids=lambda error: type(error).__name__)
    def test_opens_without_shared_usage_and_logs_one_info_line(self, caplog, error):
        caplog.set_level(logging.INFO, logger=LOG)
        query = OpenedPaths([three(shared=[process_item(4242, LUID_A, 9)])])
        factory = refusing(query, error)
        backend = make_backend(factory)
        assert backend.open() is True
        assert factory.calls == [win_backend.PATHS, win_backend.DEDICATED_PATHS]
        assert query.paths == win_backend.DEDICATED_PATHS
        lines = [record for record in caplog.records if record.name == LOG and OPEN_WORDS in record.getMessage()]
        assert [(record.levelno, record.getMessage()) for record in lines] == [(logging.INFO, SHARED_LINE + str(error))]

    def test_after_the_fallback_per_pid_shared_is_always_none(self, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        query = OpenedPaths([three(processes=[process_item(4242, LUID_A, 300)],
                                   shared=[process_item(4242, LUID_A, 9)])])
        backend = make_backend(refusing(query, PdhError('no shared counter')))
        assert backend.open() is True
        readings = [backend.sample(OURS) for _ in range(4)]
        assert [reading.per_pid_shared for reading in readings] == [None] * 4
        assert all(reading.per_pid == {KEY_A: {4242: 300}, KEY_B: {}} for reading in readings)
        assert sum(message.startswith(SHARED_LINE) for message in caplog.messages) == 1

    @pytest.mark.parametrize('error', OPEN_ERRORS, ids=lambda error: type(error).__name__)
    def test_when_the_retry_fails_too_only_the_existing_line_is_logged(self, caplog, error):
        caplog.set_level(logging.INFO, logger=LOG)
        factory = refusing(OpenedPaths([three()]), PdhError('first'), error)
        assert make_backend(factory).open() is False
        assert factory.calls == [win_backend.PATHS, win_backend.DEDICATED_PATHS]
        lines = [message for message in caplog.messages if OPEN_WORDS in message]
        assert lines == [OPEN_LINE + str(error)]

    def test_a_query_that_opens_first_time_is_not_retried_or_logged(self, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        query = FakeQuery([three()])
        calls = []

        def factory(paths):
            calls.append(tuple(paths))
            return query(paths)

        assert make_backend(factory).open() is True
        assert calls == [win_backend.PATHS]
        assert not [message for message in caplog.messages if OPEN_WORDS in message]

    def test_a_real_pdh_query_refusing_shared_usage_is_closed_then_reopened(self, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        adapters = FakeArray(ADAPTERS)
        processes = FakeArray([process_item(4242, LUID_A, 300)])
        # Counter handles count every add, the refused one included: the reopened query's two are 4 and 5.
        api = FakeApi(fail='add ' + SHARED, arrays={4: adapters, 5: processes})
        backend = make_backend(lambda paths: win_pdh.PdhQuery(paths, api=api))
        assert backend.open() is True
        reading = backend.sample(OURS)
        assert reading.per_pid == {KEY_A: {4242: 300}, KEY_B: {}} and reading.per_pid_shared is None
        assert api.log[:5] == ['open', 'add ' + ADAPTER, 'add ' + PROCESS, 'add ' + SHARED, 'close']
        assert api.log[5:8] == ['open', 'add ' + ADAPTER, 'add ' + PROCESS]
        assert [message for message in caplog.messages if OPEN_WORDS in message] == [
            SHARED_LINE + f"PdhAddEnglishCounterW failed for {SHARED} (0xC0000BB8)"]
