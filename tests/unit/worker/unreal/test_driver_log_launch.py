# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``launch_driver(render_log, warmup_log)``: the chosen adapter's driver for one launch (FR-MT-V-1).

The render log is read first; the warm-up log only when the render log has no ``Found`` line (logged at
INFO). A missing or ``Unknown`` driver is logged at WARNING with its reason and reported as null; an
unreadable log is logged at WARNING and read as a log without adapter lines.
"""
import dataclasses
import logging
from pathlib import Path

import pytest

from sethlans_worker_agent.unreal_executor import driver_log
from sethlans_worker_agent.unreal_executor.driver_log import DriverReading

FIXTURES = Path(__file__).parent / 'fixtures' / 'd3d12_logs'
LOGGER = driver_log.__name__
NVIDIA = '32.0.16.1692'
AMD_DISCRETE = '32.0.22029.1019'
P = '[2026.11.17-18.27.59:807][  0]'
FOUND_0 = f'{P}LogD3D12RHI: Found D3D12 adapter 0: NVIDIA GeForce RTX 5090 (VendorId: 10de, DeviceId: 2b85'
DRIVER_0 = f'{P}LogD3D12RHI:   Driver Version: 616.92 (internal:1.1.1.1, unified:616.92)'
UNKNOWN_WHY = 'The graphics driver version is unknown ({}); it is reported as null.'
NO_LINES = 'no D3D12 adapter lines in the log'
WARMUP_INFO = 'The render log has no D3D12 adapter lines; reading the driver version from the warm-up log.'


def fixture(name):
    return FIXTURES / name


def write_log(tmp_path, *lines):
    path = tmp_path / 'render.log'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == level]


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    return caplog


class TestSources:

    def test_the_render_log_is_the_source(self, logs):
        reading = driver_log.launch_driver(fixture('nvidia.txt'), fixture('amd_discrete_modelled.txt'))
        assert reading == DriverReading(version=NVIDIA, source=driver_log.SOURCE_RENDER)
        assert messages(logs, logging.INFO) == [] and messages(logs, logging.WARNING) == []

    def test_the_warmup_log_is_not_opened_when_the_render_log_has_adapters(self, tmp_path, logs):
        reading = driver_log.launch_driver(fixture('nvidia.txt'), tmp_path / 'missing.log')
        assert reading == DriverReading(NVIDIA, 'render') and messages(logs, logging.WARNING) == []

    def test_the_warmup_log_when_the_render_log_has_no_found_line(self, logs):
        reading = driver_log.launch_driver(fixture('no_adapter_lines.txt'), fixture('amd_discrete_modelled.txt'))
        assert reading == DriverReading(version=AMD_DISCRETE, source=driver_log.SOURCE_WARMUP)
        assert messages(logs, logging.INFO) == [WARMUP_INFO] and messages(logs, logging.WARNING) == []

    def test_found_lines_without_a_driver_keep_the_render_log(self, logs):
        """The render log has Found lines (only the Basic Render Driver): the warm-up log is not read."""
        reading = driver_log.launch_driver(fixture('basic_render_driver.txt'), fixture('nvidia.txt'))
        assert reading == DriverReading(None, 'render') and messages(logs, logging.INFO) == []

    def test_found_lines_without_a_chosen_line_keep_the_render_log(self, tmp_path, logs):
        reading = driver_log.launch_driver(write_log(tmp_path, FOUND_0, DRIVER_0), fixture('nvidia.txt'))
        assert reading == DriverReading(None, 'render') and messages(logs, logging.INFO) == []

    def test_no_warmup_log_given(self, logs):
        reading = driver_log.launch_driver(fixture('no_adapter_lines.txt'))
        assert reading == DriverReading(None, None)
        assert messages(logs, logging.INFO) == [] and messages(logs, logging.WARNING) == [UNKNOWN_WHY.format(NO_LINES)]

    def test_neither_log_has_adapter_lines(self, logs):
        reading = driver_log.launch_driver(fixture('no_adapter_lines.txt'), fixture('no_adapter_lines.txt'))
        assert reading == DriverReading(None, None)
        assert messages(logs, logging.INFO) == [WARMUP_INFO]
        assert messages(logs, logging.WARNING) == [UNKNOWN_WHY.format(NO_LINES)]

    def test_the_reading_is_frozen(self):
        reading = driver_log.launch_driver(fixture('nvidia.txt'))
        with pytest.raises(dataclasses.FrozenInstanceError):
            reading.version = 'x'


class TestWarningReasons:

    @pytest.mark.parametrize('name, reason', [
        ('basic_render_driver.txt', 'no driver line for the chosen adapter 0'),
        ('driver_unknown.txt', 'Unreal logged the driver version as Unknown'),
        ('no_adapter_lines.txt', NO_LINES),
    ], ids=['basic_render_driver', 'unknown', 'no_adapter_lines'])
    def test_fixture_reasons(self, name, reason, logs):
        assert driver_log.launch_driver(fixture(name)).version is None
        assert messages(logs, logging.WARNING) == [UNKNOWN_WHY.format(reason)]

    def test_no_chosen_line(self, tmp_path, logs):
        reading = driver_log.launch_driver(write_log(tmp_path, FOUND_0, DRIVER_0))
        assert reading == DriverReading(None, 'render')
        assert messages(logs, logging.WARNING) == [UNKNOWN_WHY.format("no 'Chosen D3D12 Adapter Id' line in the log")]

    def test_chosen_adapter_without_a_found_line(self, tmp_path, logs):
        log = write_log(tmp_path, FOUND_0, DRIVER_0, f'{P}LogD3D12RHI: Chosen D3D12 Adapter Id = 5')
        assert driver_log.launch_driver(log) == DriverReading(None, 'render')
        assert messages(logs, logging.WARNING) == [
            UNKNOWN_WHY.format("no 'Found D3D12 adapter 5' line for the chosen adapter")]

    def test_a_value_outside_the_bounds_is_no_driver_line(self, tmp_path, logs):
        log = write_log(tmp_path, FOUND_0, f'{P}LogD3D12RHI:   Driver Version: 1 (internal:{"9" * 41}, unified:1)',
                        f'{P}LogD3D12RHI: Chosen D3D12 Adapter Id = 0')
        assert driver_log.launch_driver(log) == DriverReading(None, 'render')
        assert messages(logs, logging.WARNING) == [UNKNOWN_WHY.format('no driver line for the chosen adapter 0')]

    def test_unknown_from_the_warmup_log(self, logs):
        reading = driver_log.launch_driver(fixture('no_adapter_lines.txt'), fixture('driver_unknown.txt'))
        assert reading == DriverReading(None, 'warmup')
        assert messages(logs, logging.WARNING) == [UNKNOWN_WHY.format('Unreal logged the driver version as Unknown')]


class TestUnreadableLog:

    def test_an_unreadable_render_log_falls_back_to_the_warmup_log(self, tmp_path, logs):
        missing = tmp_path / 'missing.log'
        reading = driver_log.launch_driver(missing, fixture('nvidia.txt'))
        assert reading == DriverReading(NVIDIA, 'warmup')
        warnings = messages(logs, logging.WARNING)
        assert len(warnings) == 1
        assert warnings[0].startswith(f'Could not read the render log for the driver version ({missing}): ')
        assert messages(logs, logging.INFO) == [WARMUP_INFO]

    def test_an_unreadable_render_log_without_a_warmup_log(self, tmp_path, logs):
        reading = driver_log.launch_driver(tmp_path / 'missing.log')
        assert reading == DriverReading(None, None)
        warnings = messages(logs, logging.WARNING)
        assert len(warnings) == 2 and warnings[0].startswith('Could not read the render log')
        assert warnings[1] == UNKNOWN_WHY.format(NO_LINES)

    def test_both_logs_unreadable(self, tmp_path, logs):
        reading = driver_log.launch_driver(tmp_path / 'render.log', tmp_path)   # a directory cannot be read either
        assert reading == DriverReading(None, None)
        warnings = messages(logs, logging.WARNING)
        assert [w.split(' (')[0] for w in warnings[:2]] == [
            'Could not read the render log for the driver version',
            'Could not read the warmup log for the driver version']
        assert warnings[2] == UNKNOWN_WHY.format(NO_LINES) and len(warnings) == 3

    def test_an_unreadable_warmup_log(self, tmp_path, logs):
        reading = driver_log.launch_driver(fixture('no_adapter_lines.txt'), tmp_path / 'warmup.log')
        assert reading == DriverReading(None, None)
        warnings = messages(logs, logging.WARNING)
        assert warnings[0].startswith('Could not read the warmup log') and warnings[1] == UNKNOWN_WHY.format(NO_LINES)
