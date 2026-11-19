# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The worker integration suite never runs the real graphics card probe.

``job_processor.init_capacity`` logs the card list once per process through
``cards.startup_log.log_once_at_start``, which calls ``cards.os_probe.probe``
(DXGI, sysfs or ``system_profiler``). The autouse ``_quiet_card_startup_log``
fixture in this package's conftest marks that log as done before each test.

``log_once_at_start`` swallows every exception, so a probe that raises would
not fail the test; these tests assert on the mock's call count instead.
"""

from sethlans_worker_agent.cards import startup_log

from ._capacity_harness import install_fake_capacity

PROBE_TARGET = 'sethlans_worker_agent.cards.os_probe.probe'


class TestCardStartupLogQuietIntegration:
    """Building capacity through the harness leaves the OS probe alone."""

    def test_harness_capacity_build_does_not_call_os_probe(self, mocker):
        probe = mocker.patch(PROBE_TARGET, return_value=())

        install_fake_capacity(mocker, gpu_count=1, cpu_cores=8)

        probe.assert_not_called()

    def test_rearmed_log_calls_patched_probe_once(self, mocker):
        """Control: the patch target is the one the startup log really uses."""
        probe = mocker.patch(PROBE_TARGET, return_value=())
        startup_log.reset_for_tests()

        install_fake_capacity(mocker, gpu_count=1, cpu_cores=8)
        install_fake_capacity(mocker, gpu_count=1, cpu_cores=8)

        probe.assert_called_once_with()
