# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``nvidia-smi -q -d PIDS`` parser (GPU memory reader FR-GN-3) on hand-made listings (C-20)."""
import pytest

from sethlans_worker_agent.gpu_memory.nvidia_pids import parse_pids
from tests.unit.worker.gpu_memory._nvidia_fixtures import (BUS_A, BUS_B, BUSY, HEADER, IDLE, KEY_A, KEY_B, gpu_block,
                                                           listing)

KNOWN = {KEY_A, KEY_B}


def parse(text, known=KNOWN):
    return parse_pids(text, known_keys=known)


def raw(body, attached=1):
    """A hand-written block body after the header (for layouts ``gpu_block`` cannot make)."""
    return HEADER + f"Attached GPUs : {attached}\n" + body


class TestLayout:

    def test_pairs_per_card_key(self):
        assert parse(BUSY) == {KEY_A: ((4100, 600), (4200, 300)), KEY_B: ((4300, 50),)}

    def test_idle_card_has_no_process(self):
        assert parse(IDLE) == {KEY_A: ()}

    def test_upper_case_eight_digit_domain_gives_the_normal_key(self):
        key = 'pci:0000:0a:00.0'
        assert parse(listing(gpu_block('00000000:0A:00.0', (1, 'G', 'x', '1 MiB'))), {key}) == {key: ((1, 1),)}

    def test_five_digit_domain(self):
        key = 'pci:10000:e1:00.0'
        assert list(parse(listing(gpu_block('00010000:E1:00.0')), {key})) == [key]

    @pytest.mark.parametrize('text', ['', 'garbage\n', HEADER + "Attached GPUs : 0\n"])
    def test_nothing_listed(self, text):
        assert parse(text) == {}

    def test_header_fields_are_never_pairs(self):
        assert parse(raw("Process ID : 9\nUsed GPU Memory : 9 MiB\n" + gpu_block(BUS_A))) == {KEY_A: ()}


class TestOnlyInsideABusIdBlock:

    def test_a_pair_before_any_bus_id_line_is_ignored(self):
        assert parse(raw("Process ID : 77\nUsed GPU Memory : 999 MiB\n"
                         + gpu_block(BUS_A, (4100, 'G', 'render-app', '600 MiB')))) == {KEY_A: ((4100, 600),)}

    @pytest.mark.parametrize('line', ['GPU 0000:01:00', 'GPU 00000000:01:00.8', 'GPU', 'GPU 00000000:01:00.0 extra',
                                      'Attached GPUs : 1'])
    def test_a_line_that_is_not_a_bus_id_opens_no_block(self, line):
        text = raw(f"{line}\n    Processes\n        Process ID : 5\n            Used GPU Memory : 5 MiB\n", attached=0)
        assert parse(text) == {}

    def test_the_same_card_listed_twice_is_not_attributed(self):
        text = listing(gpu_block(BUS_A, (1, 'G', 'a', '1 MiB')), gpu_block(BUS_A, (2, 'G', 'b', '2 MiB')))
        assert parse(text) == {KEY_A: None}


class TestDistrustedListing:
    """A process can put newlines in its own name to fake lines: the whole listing is then distrusted."""

    def test_more_blocks_than_attached_gpus(self):
        fake = 'x\nGPU 00000000:02:00.0\n        Process ID : 4300\n            Used GPU Memory : 1 MiB'
        assert parse(listing(gpu_block(BUS_A, (4100, 'G', fake, '600 MiB')))) == {}  # Attached GPUs : 1, two blocks

    @pytest.mark.parametrize('attached', ['3', '0', 'N/A', ''])
    def test_attached_gpus_not_matching_the_blocks(self, attached):
        text = HEADER + f"Attached GPUs : {attached}\n" + gpu_block(BUS_A) + gpu_block(BUS_B)
        assert parse(text) == {}

    def test_no_attached_gpus_line(self):
        assert parse(HEADER + gpu_block(BUS_A)) == {}

    def test_attached_gpus_given_twice(self):
        assert parse(raw("Attached GPUs : 1\n" + gpu_block(BUS_A))) == {}

    def test_a_bus_id_the_card_query_does_not_list(self):
        assert parse(BUSY, known={KEY_A}) == {}

    def test_an_invalid_bus_id(self):
        assert parse(listing(gpu_block(BUS_A), gpu_block('00000000:01:20.0'))) == {}

    def test_a_fake_block_name_with_matching_count_still_needs_a_known_key(self):
        fake = 'x\nGPU 00000000:09:00.0\n        Process ID : 1\n            Used GPU Memory : 1 MiB'
        assert parse(raw(gpu_block(BUS_A, (4100, 'G', fake, '600 MiB')) + gpu_block(BUS_B), attached=3)) == {}


class TestNamesAndOtherFieldsIgnored:

    @pytest.mark.parametrize('name', ['Process ID : 77', 'Used GPU Memory : 999 MiB', 'GPU 00000000:02:00.0',
                                      'tool: with: colons', ''])
    def test_name_values_never_count(self, name):
        assert parse(listing(gpu_block(BUS_A, (4100, 'C+G', name, '600 MiB')))) == {KEY_A: ((4100, 600),)}

    def test_no_name_is_kept_anywhere(self):
        assert 'render-app' not in repr(parse(listing(gpu_block(BUS_A, (4100, 'G', 'render-app', '600 MiB')))))


class TestPairs:

    @pytest.mark.parametrize('used', ['N/A', '[N/A]', 'Not available in WDDM driver model', '12.5 MiB', '-5 MiB',
                                      '600 GiB', ''])
    def test_a_memory_that_is_not_an_integer_drops_the_pair_and_the_card(self, used):
        text = listing(gpu_block(BUS_A, (4100, 'G', 'a', used), (4200, 'G', 'b', '10 MiB')),
                       gpu_block(BUS_B, (4300, 'C', 'c', '50 MiB')))
        assert parse(text) == {KEY_A: None, KEY_B: ((4300, 50),)}

    @pytest.mark.parametrize('pid', ['N/A', '-1', '12a', '', '12345678901'])
    def test_a_pid_that_is_not_an_integer_drops_the_pair_and_the_card(self, pid):
        assert parse(listing(gpu_block(BUS_A, (pid, 'G', 'a', '10 MiB')))) == {KEY_A: None}

    def test_a_pid_listed_twice_on_one_card_is_not_attributed(self):
        text = listing(gpu_block(BUS_A, (4100, 'G', 'a', '600 MiB'), (4100, 'C', 'a', '9000 MiB')),
                       gpu_block(BUS_B, (4100, 'C', 'a', '50 MiB')))
        assert parse(text) == {KEY_A: None, KEY_B: ((4100, 50),)}  # one pid on two cards is normal

    def test_a_pid_without_a_memory_line_is_not_attributed(self):
        assert parse(raw(f"GPU {BUS_A}\n    Processes\n        Process ID : 5\n            Type : G\n")) == {KEY_A: None}

    def test_a_pid_followed_by_another_pid_is_not_attributed(self):
        text = raw(f"GPU {BUS_A}\n        Process ID : 5\n        Process ID : 6\n        Used GPU Memory : 1 MiB\n")
        assert parse(text) == {KEY_A: None}

    def test_a_memory_line_without_a_pid_is_not_attributed(self):
        assert parse(raw(f"GPU {BUS_A}\n    Processes\n            Used GPU Memory : 5 MiB\n")) == {KEY_A: None}

    def test_a_pid_left_open_at_the_next_block_spoils_only_its_own_card(self):
        text = raw(f"GPU {BUS_A}\n        Process ID : 5\n" + gpu_block(BUS_B, (6, 'G', 'b', '7 MiB')), attached=2)
        assert parse(text) == {KEY_A: None, KEY_B: ((6, 7),)}
