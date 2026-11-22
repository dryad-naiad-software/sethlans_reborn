# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The graphics driver version parsed from UE's D3D12 adapter lines (per-machine memory test D3, FR-MT-V-1).

``driver_versions`` maps every ``Found`` adapter to the ``internal:`` value of the first driver line of its
block (``None`` for the Basic Render Driver, ``Unknown`` or a value outside the bounds); ``chosen_driver``
is that value for the ``Chosen`` adapter. Both raise ``OSError`` for a log that cannot be opened.
Each fixture's ``#`` header states the expected result; the table below repeats it.
"""
from pathlib import Path

import pytest

from sethlans_worker_agent.unreal_executor import driver_log

FIXTURES = Path(__file__).parent / 'fixtures' / 'd3d12_logs'
NVIDIA = '32.0.16.1692'
AMD_IGPU = '32.0.21042.62'
AMD_DISCRETE = '32.0.22029.1019'
FORTY = 'A123456789B123456789C123456789D123456789'
REAL = {0: NVIDIA, 1: AMD_IGPU, 2: None, 3: NVIDIA}
P = '[2026.11.17-18.27.59:807][  0]'


def found(index, name='NVIDIA GeForce RTX 5090'):
    return f'{P}LogD3D12RHI: Found D3D12 adapter {index}: {name} (VendorId: 10de, DeviceId: 2b85, SubSysId: 0, Revision: 00a1'


def driver(internal, prefix=P):
    return f'{prefix}LogD3D12RHI:   Driver Version: 616.92 (internal:{internal}, unified:616.92)'


def chosen(index):
    return f'{P}LogD3D12RHI: Chosen D3D12 Adapter Id = {index}'


def write_log(tmp_path, *lines, newline='\n'):
    path = tmp_path / 'render.log'
    path.write_bytes(newline.join(lines).encode('utf-8') + newline.encode('utf-8'))
    return path


# (fixture, driver_versions, chosen_driver), as each fixture's header states
TABLE = [
    ('nvidia.txt', {0: NVIDIA, 1: None}, NVIDIA),
    ('amd_integrated.txt', REAL, AMD_IGPU),
    ('amd_discrete_modelled.txt', {0: AMD_DISCRETE, 1: None}, AMD_DISCRETE),
    ('basic_render_driver.txt', {0: None}, None),
    ('duplicate_adapter.txt', REAL, NVIDIA),
    ('driver_unknown.txt', {0: None, 1: AMD_IGPU}, None),
    ('echo_lines.txt', REAL, NVIDIA),
    ('bounds.txt', {0: None, 1: None, 2: FORTY}, FORTY),
    ('no_adapter_lines.txt', {}, None),
    ('real_enumeration.txt', REAL, NVIDIA),
]
IDS = [row[0].removesuffix('.txt') for row in TABLE]


class TestFixtures:

    @pytest.mark.parametrize('name, versions, _chosen', TABLE, ids=IDS)
    def test_driver_versions(self, name, versions, _chosen):
        assert driver_log.driver_versions(FIXTURES / name) == versions

    @pytest.mark.parametrize('name, _versions, expected', TABLE, ids=IDS)
    def test_chosen_driver(self, name, _versions, expected):
        assert driver_log.chosen_driver(FIXTURES / name) == expected

    def test_every_fixture_is_in_the_table(self):
        assert sorted(p.name for p in FIXTURES.glob('*.txt')) == sorted(row[0] for row in TABLE)

    def test_unknown_is_null_not_the_string(self):
        versions = driver_log.driver_versions(FIXTURES / 'driver_unknown.txt')
        assert versions[0] is None and driver_log.UNKNOWN_DRIVER not in versions.values()

    def test_echoed_values_never_appear(self):
        versions = driver_log.driver_versions(FIXTURES / 'echo_lines.txt')
        assert not {'6.6.6.6', '7.7.7.7', '8.8.8.8', '9.9.9.9'} & set(versions.values())

    def test_bounds_fixture_values_are_40_and_41_characters(self):
        text = (FIXTURES / 'bounds.txt').read_text(encoding='utf-8')
        assert len(FORTY) == 40 and f'internal:{FORTY}E,' in text and f'internal:{FORTY},' in text

    def test_a_three_digit_found_index_is_not_an_adapter(self):
        assert 123 not in driver_log.driver_versions(FIXTURES / 'bounds.txt')


class TestBlockRules:

    def test_a_driver_line_before_the_first_found_line_counts_for_nothing(self, tmp_path):
        log = write_log(tmp_path, driver('1.1.1.1'), found(0), chosen(0))
        assert driver_log.driver_versions(log) == {0: None}
        assert driver_log.chosen_driver(log) is None

    def test_a_driver_line_after_the_chosen_line_counts_for_nothing(self, tmp_path):
        log = write_log(tmp_path, found(0), chosen(0), driver('1.1.1.1'))
        assert driver_log.driver_versions(log) == {0: None}

    def test_only_the_first_driver_line_of_a_block_counts(self, tmp_path):
        log = write_log(tmp_path, found(0), driver('1.1.1.1'), driver('2.2.2.2'), chosen(0))
        assert driver_log.chosen_driver(log) == '1.1.1.1'

    def test_the_next_found_line_closes_the_block(self, tmp_path):
        log = write_log(tmp_path, found(0), found(1), driver('1.1.1.1'), chosen(0))
        assert driver_log.driver_versions(log) == {0: None, 1: '1.1.1.1'}
        assert driver_log.chosen_driver(log) is None

    def test_a_repeated_index_keeps_its_first_block(self, tmp_path):
        log = write_log(tmp_path, found(0), driver('1.1.1.1'), found(0), driver('2.2.2.2'), chosen(0))
        assert driver_log.driver_versions(log) == {0: '1.1.1.1'}

    def test_a_repeated_index_closes_the_open_block(self, tmp_path):
        log = write_log(tmp_path, found(0), found(1), found(0), driver('2.2.2.2'), chosen(1))
        assert driver_log.driver_versions(log) == {0: None, 1: None}

    def test_a_repeated_chosen_line_keeps_the_first(self, tmp_path):
        log = write_log(tmp_path, found(0), driver('1.1.1.1'), found(1), driver('2.2.2.2'), chosen(0), chosen(1))
        assert driver_log.chosen_driver(log) == '1.1.1.1'

    def test_two_digit_indexes_are_adapters(self, tmp_path):
        log = write_log(tmp_path, found(10), driver('1.1.1.1'), chosen(10))
        assert driver_log.driver_versions(log) == {10: '1.1.1.1'}
        assert driver_log.chosen_driver(log) == '1.1.1.1'

    def test_lines_without_the_time_prefix_count(self, tmp_path):
        log = write_log(tmp_path, *(line.replace(P, '') for line in (found(0), driver('1.1.1.1'), chosen(0))))
        assert driver_log.chosen_driver(log) == '1.1.1.1'

    def test_crlf_line_endings(self, tmp_path):
        log = write_log(tmp_path, found(0), driver('1.1.1.1'), chosen(0), newline='\r\n')
        assert driver_log.chosen_driver(log) == '1.1.1.1'

    def test_a_driver_line_without_unified_does_not_match(self, tmp_path):
        log = write_log(tmp_path, found(0), f'{P}LogD3D12RHI:   Driver Version: 616.92 (internal:1.1.1.1)', chosen(0))
        assert driver_log.driver_versions(log) == {0: None}

    def test_a_logrhi_driver_line_inside_a_block_does_not_match(self, tmp_path):
        log = write_log(tmp_path, found(0), f'{P}LogRHI:   Driver Version: 616.92 (internal:1.1.1.1, unified:616.92)',
                        chosen(0))
        assert driver_log.driver_versions(log) == {0: None}


class TestChosenDriverWithoutAnswer:

    def test_no_chosen_line(self, tmp_path):
        assert driver_log.chosen_driver(write_log(tmp_path, found(0), driver('1.1.1.1'))) is None

    def test_chosen_index_without_a_found_line(self, tmp_path):
        assert driver_log.chosen_driver(write_log(tmp_path, found(0), driver('1.1.1.1'), chosen(5))) is None

    def test_an_empty_log(self, tmp_path):
        log = tmp_path / 'empty.log'
        log.write_bytes(b'')
        assert driver_log.driver_versions(log) == {} and driver_log.chosen_driver(log) is None


class TestUnreadableLog:

    @pytest.mark.parametrize('parse', [driver_log.driver_versions, driver_log.chosen_driver],
                             ids=['driver_versions', 'chosen_driver'])
    def test_a_missing_log_raises_oserror(self, tmp_path, parse):
        with pytest.raises(OSError):
            parse(tmp_path / 'missing.log')

    @pytest.mark.parametrize('parse', [driver_log.driver_versions, driver_log.chosen_driver],
                             ids=['driver_versions', 'chosen_driver'])
    def test_a_directory_raises_oserror(self, tmp_path, parse):
        with pytest.raises(OSError):
            parse(tmp_path)
