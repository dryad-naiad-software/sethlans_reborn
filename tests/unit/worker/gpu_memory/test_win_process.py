# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``gpu_memory.win_process``: the per-process PDH item helpers moved out of ``win_backend`` (per machine memory
test FR-MT-S-1), and an import that needs no DLL or Windows-only module. Made-up PDH items only; runs on every
OS.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sethlans_worker_agent.gpu_memory import win_backend, win_process
from sethlans_worker_agent.gpu_memory.win_pdh import (
    PDH_CSTATUS_NEW_DATA, PDH_CSTATUS_NO_INSTANCE, PDH_CSTATUS_VALID_DATA)
from tests.unit.worker.gpu_memory._win_fakes import KEY_A, KEY_B, LUID_A, LUID_B, MIB, OTHER_PID, OURS, os_card
from tests.unit.worker.gpu_memory._win_fakes import process_item

REPO = Path(__file__).resolve().parents[4]
A, B = win_backend.luid_value(LUID_A), win_backend.luid_value(LUID_B)


class TestNames:

    def test_hex_luid_joins_high_and_low(self):
        assert win_process.hex_luid('00000001', '0001a2b3') == (1 << 32) | 0x1A2B3
        assert win_process.hex_luid('FFFFFFFF', 'FFFFFFFF') == 0xFFFFFFFFFFFFFFFF

    @pytest.mark.parametrize('name', ['pid_4242_luid_0x00000000_0x0001A2B3_phys_0',
                                      'pid_4242_luid_0x00000000_0x0001a2b3_phys_7',
                                      'PID_4242_LUID_0X00000000_0X0001A2B3_PHYS_0'])
    def test_process_owner_ignores_case_and_phys(self, name):
        assert win_process.process_owner(name) == (4242, A)

    @pytest.mark.parametrize('name', ['', None, 'pid__luid_0x00000000_0x0001A2B3_phys_0',
                                      'pid_1_luid_0x0_0x1_phys_0', 'pid_1_luid_0x00000000_0x0001A2B3_phys_',
                                      'pid_1_luid_0x00000000_0x0001A2B3_phys_0_x', 'luid_0x00000000_0x0001A2B3_phys_0'])
    def test_other_names_do_not_parse(self, name):
        assert win_process.process_owner(name) is None

    def test_valid_items_keeps_valid_and_new_data_of_zero_or_more(self):
        items = [('a', 5, PDH_CSTATUS_VALID_DATA), ('b', 0, PDH_CSTATUS_NEW_DATA), ('c', 9, PDH_CSTATUS_NO_INSTANCE),
                 ('d', -1, PDH_CSTATUS_VALID_DATA), ('e', 3, 0xC0000BB8)]
        assert list(win_process.valid_items(items)) == [('a', 5), ('b', 0)]


class TestByPid:

    def test_bytes_per_luid_and_pid_summed_over_phys(self):
        items = [process_item(4242, LUID_A, 10, phys=0), process_item(4242, LUID_A, 5, phys=1),
                 process_item(4343, LUID_B, 2), process_item(OTHER_PID, LUID_A, 99)]
        assert win_process.process_usage_by_pid(items, OURS) == {A: {4242: 15 * MIB}, B: {4343: 2 * MIB}}

    def test_by_pid_or_none(self):
        items = [process_item(4242, LUID_A, 1)]
        assert win_process.by_pid_or_none(None, OURS) is None
        assert win_process.by_pid_or_none([], OURS) == {}
        assert win_process.by_pid_or_none(items, OURS) == win_process.process_usage_by_pid(items, OURS)

    def test_by_pid_or_none_reads_a_generator_once(self):
        items = (item for item in [process_item(4242, LUID_A, 1), process_item(4343, LUID_A, 2)])
        assert win_process.by_pid_or_none(items, OURS) == {A: {4242: MIB, 4343: 2 * MIB}}

    def test_luid_sums_and_process_usage(self):
        items = [process_item(4242, LUID_A, 10), process_item(4343, LUID_A, 20), process_item(4343, LUID_B, 1)]
        assert win_process.luid_sums({A: {4242: 1, 4343: 2}, B: {}}) == {A: 3, B: 0}
        assert win_process.luid_sums(win_process.process_usage_by_pid(items, OURS)) == {A: 30 * MIB, B: MIB}


class TestPerPidMap:

    CARDS = (win_backend.Card(os_card(0, KEY_A), A), win_backend.Card(os_card(2, KEY_B, 'Test Card B'), B),
             win_backend.Card(os_card(3, None, 'Test Card C'), 7))

    def test_none_stays_none(self):
        assert win_process.per_pid_map(self.CARDS, None) is None

    def test_mib_per_pid_by_card_key_floored_and_keyless_cards_left_out(self):
        by_pid = {A: {4242: 3 * MIB + MIB - 1, 4343: MIB - 1}, 7: {4242: 50 * MIB}, 99: {4242: MIB}}
        assert win_process.per_pid_map(self.CARDS, by_pid) == {KEY_A: {4242: 3, 4343: 0}, KEY_B: {}}

    def test_an_empty_map_gives_every_keyed_card_an_empty_entry(self):
        assert win_process.per_pid_map(self.CARDS, {}) == {KEY_A: {}, KEY_B: {}}

    def test_no_cards_gives_an_empty_map(self):
        assert win_process.per_pid_map((), {A: {4242: MIB}}) == {}


_CHILD = r"""
import ctypes, json, sys

loads = []
_cdll_init = ctypes.CDLL.__init__


def record_load(self, name, *args, **kwargs):
    loads.append(str(name))
    _cdll_init(self, name, *args, **kwargs)


ctypes.CDLL.__init__ = record_load        # WinDLL and windll.<name> come through here
sys.modules['ctypes.wintypes'] = None     # a Windows-only import would now fail
sys.platform = 'linux'

from sethlans_worker_agent.gpu_memory import win_process

name = 'pid_4242_luid_0x00000000_0x0001A2B3_phys_0'
print('RESULT ' + json.dumps({
    'modules': sorted(m for m in sys.modules if m.startswith('sethlans_worker_agent.')),
    'loads': loads,
    'owner': win_process.process_owner(name),
    'by_pid': {str(k): v for k, v in win_process.by_pid_or_none([(name, 2 * 1048576, 0)], frozenset({4242})).items()},
}))
"""


def test_win_process_imports_and_works_without_a_dll_or_windows_modules(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith('SETHLANS_')}
    env['PYTHONPATH'] = os.pathsep.join([str(REPO / 'worker'), str(REPO)])
    completed = subprocess.run([sys.executable, '-c', _CHILD], capture_output=True, text=True, timeout=60,
                               env=env, cwd=str(tmp_path))
    assert completed.returncode == 0, completed.stderr[-4000:]
    [line] = [line for line in completed.stdout.splitlines() if line.startswith('RESULT ')]
    result = json.loads(line[len('RESULT '):])
    assert result['loads'] == []
    assert 'sethlans_worker_agent.gpu_memory.win_backend' not in result['modules'], result['modules']
    assert 'sethlans_worker_agent.gpu_memory.win_dxgi' not in result['modules'], result['modules']
    assert result['owner'] == [4242, 0x0001A2B3]
    assert result['by_pid'] == {str(0x0001A2B3): {'4242': 2 * MIB}}
