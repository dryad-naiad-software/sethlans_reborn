# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``SETHLANS_COMPUTE_CARDS`` naming cards of more than one brand (unreal-render-per-card FR-PC-BR-3, CS FR-CC-2).

The variable names addresses only, so the brands show once an inventory lists the cards. From then on, for the
process: one ERROR line naming the brands, ``method = cpu`` (the variable is an invalid value) and Unreal off with
its own reason. The worker's start builds its first profile on the CPU already.
"""
import dataclasses
import logging

import pytest

from sethlans_worker_agent import config_compute, job_processor, system_monitor
from sethlans_worker_agent.cards import env_brands, matching, os_probe, unreal_cards
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker._card_choice import card, inv, rtx_a, rtx_b, xt_03

ENV = {config_compute.ENV_METHOD: 'gpu', config_compute.ENV_CARDS: '0000:01:00.0,0000:03:00.0'}
ONE_BRAND = {config_compute.ENV_METHOD: 'gpu', config_compute.ENV_CARDS: '0000:01:00.0,0000:02:00.0'}
LOGGER = 'sethlans_worker_agent.cards.env_brands'


@pytest.fixture(autouse=True)
def _fresh_once_log():
    os_probe.reset_logged_for_tests()
    yield
    os_probe.reset_logged_for_tests()


def _unreal(item, index):
    """The card with a DXGI index, so it would be an Unreal card."""
    return dataclasses.replace(item, dxgi_index=index)


def _errors(caplog):
    return [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR
            and record.name == LOGGER]


def test_the_constant_is_config_computes():
    assert env_brands.ENV_CARDS == config_compute.ENV_CARDS


class TestEvaluation:

    def test_two_brands_named_run_on_the_cpu_without_unreal(self, caplog):
        machine = inv(_unreal(rtx_a(), 0), _unreal(xt_03(), 1))
        section = config_compute.parse({}, ENV)
        assert section.method == 'gpu'  # no inventory seen yet

        with caplog.at_level(logging.ERROR, logger=LOGGER):
            result = matching.evaluate(section, machine)

        assert result.section.method == 'cpu' and config_compute.ENV_CARDS in result.section.invalid
        assert result.blender_cards == () and result.unreal_cards == ()
        assert _errors(caplog) == ["SETHLANS_COMPUTE_CARDS names graphics cards of more than one brand (AMD, NVIDIA); "
                                   "this is not supported. This worker renders on the CPU only and Unreal is off "
                                   "until it is fixed."]

    def test_the_finding_holds_for_the_process(self, caplog):
        matching.evaluate(config_compute.parse({}, ENV), inv(rtx_a(), xt_03()))
        caplog.clear()

        with caplog.at_level(logging.ERROR, logger=LOGGER):
            later = config_compute.parse({}, ENV)
            matching.evaluate(later, inv(rtx_a()))  # the AMD card has gone: still invalid

        assert later.method == 'cpu' and later.invalid == (config_compute.ENV_CARDS,)
        assert _errors(caplog) == []  # the brand line is logged once

    def test_unreal_is_disabled_with_the_reason(self):
        machine = inv(_unreal(rtx_a(), 0), _unreal(xt_03(), 1))

        assert unreal_cards.evaluate(config_compute.parse({}, ENV), machine) == \
            ((), "graphics cards of more than one brand are ticked (not supported)")

    def test_an_unknown_card_beside_a_known_one_is_invalid(self):
        unknown = card('pci:0000:03:00.0', 'Unknown card')
        result = matching.evaluate(config_compute.parse({}, ENV), inv(rtx_a(), unknown))
        assert result.section.method == 'cpu' and env_brands.seen_mixed() == ('NVIDIA', 'unknown')

    def test_one_brand_named_is_valid(self, caplog):
        machine = inv(_unreal(rtx_a(), 0), _unreal(rtx_b(), 1))

        with caplog.at_level(logging.ERROR, logger=LOGGER):
            result = matching.evaluate(config_compute.parse({}, ONE_BRAND), machine)
            cards, reason = unreal_cards.evaluate(config_compute.parse({}, ONE_BRAND), machine)

        assert result.section.method == 'gpu' and len(result.blender_cards) == 2
        assert len(cards) == 2 and reason == ''
        assert _errors(caplog) == [] and env_brands.seen_mixed() is None

    def test_a_saved_choice_is_not_this_rule(self):
        """Only the variable is checked here; a saved choice was refused when saved (FR-PC-BR-2)."""
        section = config_compute.parse({'method': 'gpu', 'cards': [{'key': 'pci:0000:01:00.0'},
                                                                   {'key': 'pci:0000:03:00.0'}]}, {})
        assert env_brands.check(section, inv(rtx_a(), xt_03())) is None


class TestStart:

    def test_the_first_profile_is_cpu_only(self, monkeypatch, mocker):
        mocker.patch.object(system_monitor, 'get_gpu_device_details', return_value=[])
        mocker.patch.object(system_monitor, 'get_cpu_thread_count', return_value=8)
        machine = CardEnv(monkeypatch, count=2)
        nvidia_probe = os_probe.probe
        monkeypatch.setattr(os_probe, 'probe', lambda: tuple(  # card 1 is an AMD card
            dataclasses.replace(item, vendor_id=0x1002) if item.key.endswith(':02:00.0') else item
            for item in nvidia_probe()))
        for name, value in ONE_BRAND.items():
            monkeypatch.setenv(name, value)

        cap = job_processor.init_capacity()

        assert machine.probes >= 1
        assert cap.profile.method == 'cpu' and cap.profile.cards == ()
        assert cap.profile.gpu_slot_count == 0
