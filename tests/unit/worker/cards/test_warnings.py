# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.warnings`` (FR-CB-5, D21, D22, AC-38).

Every row of the D22 table, the warning entry, and the log line an Eevee or
Workbench job start writes (once per job, never refused), also through
``render_script.generate_render_config_script``.
"""
import dataclasses
import logging

import pytest

from sethlans_worker_agent import render_script
from sethlans_worker_agent.cards import matching
from sethlans_worker_agent.cards import warnings as card_warnings
from sethlans_worker_agent.cards.inventory import Inventory
from tests.unit.worker._card_choice import KEY_XT_03, KEY_XT_23, inv, section, xt_03, xt_23

LOGGER = 'sethlans_worker_agent.cards.warnings'
LOG_LINE = ("Job 7 uses BLENDER_EEVEE_NEXT, which is not tied to the ticked graphics cards; it may render on an "
            "unticked card (#251).")
HELD = [{'id': 'c' * 32, 'kind': 'card_lost', 'key': KEY_XT_03, 'name': 'x'}]
BLENDER_LOST = [{'id': 'c' * 32, 'kind': 'card_lost_blender', 'key': KEY_XT_23, 'name': 'x'}]


def _one():
    return inv(xt_03())


def _two():
    return inv(xt_03(), xt_23())


# (inventory, method, ticked, alerts, expected) for each row of the D22 table, in its order.
D22_ROWS = [
    ('no card', Inventory, 'gpu', [], [], False),
    ('1 card unticked, method CPU', _one, 'cpu', [], [], True),
    ('1 card ticked, method CPU', _one, 'cpu', [KEY_XT_03], [], True),
    ('1 card ticked ok, method GPU', _one, 'gpu', [KEY_XT_03], [], False),
    ('1 card ticked held', _one, 'gpu', [KEY_XT_03], HELD, True),
    ('2 cards both ok, method GPU', _two, 'gpu', [KEY_XT_03, KEY_XT_23], [], False),
    ('2 cards both ticked, method CPU', _two, 'cpu', [KEY_XT_03, KEY_XT_23], [], True),
    ('2 cards one ticked', _two, 'gpu', [KEY_XT_03], [], True),
    ('2 cards both ticked, one blender_lost', _two, 'gpu', [KEY_XT_03, KEY_XT_23], BLENDER_LOST, True),
]


def _evaluated(make, method, ticked, alerts):
    inventory = make()
    saved = _two() if not inventory.cards else inventory
    return inventory, matching.evaluate(section(method, 'split', ticked, saved, alerts=alerts), inventory)


class TestD22:

    @pytest.mark.parametrize('name, make, method, ticked, alerts, expected', D22_ROWS, ids=[row[0] for row in D22_ROWS])
    def test_row(self, name, make, method, ticked, alerts, expected):
        inventory, result = _evaluated(make, method, ticked, alerts)
        warnings = card_warnings.build(inventory, result)
        assert card_warnings.engine_not_tied(inventory, result) is expected
        if expected:
            assert warnings == [{'code': 'engine_not_tied', 'key': None, 'text': card_warnings.ENGINE_TEXT}]
        else:
            assert warnings == []

    def test_ticked_ok_card_that_blender_does_not_offer_counts(self):
        """A ticked card Unreal may use but Blender does not offer is not a Blender card in use."""
        cards = inv(dataclasses.replace(xt_03(), blender=None))
        result = matching.evaluate(section('gpu', 'split', [KEY_XT_03], cards), cards)
        assert card_warnings.engine_not_tied(cards, result) is True

    def test_failed_evaluation_gives_no_warning(self):
        result = matching.evaluate(section('cpu', 'split', [], _one()), None)
        assert card_warnings.build(_one(), result) == []

    def test_text(self):
        assert card_warnings.ENGINE_TEXT == (
            "Eevee and Workbench jobs may render on any graphics card of this machine, including unticked ones. "
            "The ticks are firm for Cycles and Unreal only until issue #251 is fixed.")


class TestEngineLog:

    def _lines(self, caplog):
        return [record.getMessage() for record in caplog.records if record.name == LOGGER]

    def test_logged_once_per_job_while_the_condition_holds(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        card_warnings.reset_for_tests(True)
        assert card_warnings.log_engine_warning(7, 'BLENDER_EEVEE_NEXT') is True
        assert card_warnings.log_engine_warning(7, 'BLENDER_EEVEE_NEXT') is False  # the thumbnail pass
        assert self._lines(caplog) == [LOG_LINE]
        assert caplog.records[-1].levelno == logging.WARNING

    @pytest.mark.parametrize('engine', ['BLENDER_EEVEE', 'WORKBENCH', 'BLENDER_WORKBENCH'])
    def test_each_engine_not_tied(self, caplog, engine):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        card_warnings.reset_for_tests(True)
        assert card_warnings.log_engine_warning(8, engine) is True

    def test_not_logged_without_the_condition_or_for_cycles(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        card_warnings.reset_for_tests(False)
        assert card_warnings.log_engine_warning(7, 'BLENDER_EEVEE_NEXT') is False
        card_warnings.reset_for_tests(True)
        assert card_warnings.log_engine_warning(7, 'CYCLES') is False
        assert self._lines(caplog) == []

    def test_remember_updates_the_condition(self):
        card_warnings.reset_for_tests(False)
        inventory, result = _evaluated(_two, 'gpu', [KEY_XT_03], [])
        card_warnings.remember(inventory, result)
        assert card_warnings.log_engine_warning(9, 'WORKBENCH') is True
        card_warnings.remember(inventory, matching.evaluate(result.section, None))  # failed: ignored
        assert card_warnings.log_engine_warning(10, 'WORKBENCH') is True

    def test_first_use_evaluates_the_cards_once(self, mocker):
        card_warnings.reset_for_tests(None)
        probe = mocker.patch('sethlans_worker_agent.cards.os_probe.probe', return_value=())
        mocker.patch('sethlans_worker_agent.cards.startup_log.cached_blender_list', return_value=(None, None))
        assert card_warnings.log_engine_warning(11, 'WORKBENCH') is False  # no card: no warning
        assert card_warnings.log_engine_warning(12, 'WORKBENCH') is False
        assert probe.call_count == 1

    def test_evaluation_error_never_raises(self, mocker):
        card_warnings.reset_for_tests(None)
        mocker.patch('sethlans_worker_agent.cards.os_probe.probe', side_effect=RuntimeError('boom'))
        assert card_warnings.log_engine_warning(13, 'WORKBENCH') is False


class TestRenderScript:

    def test_eevee_job_start_logs_and_is_not_refused(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        card_warnings.reset_for_tests(True)
        script = render_script.generate_render_config_script(7, 'BLENDER_EEVEE_NEXT', 'GPU', {})
        assert 'BLENDER_EEVEE' in script
        assert [r.getMessage() for r in caplog.records if r.name == LOGGER] == [LOG_LINE]

    def test_cycles_job_start_does_not_log(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        card_warnings.reset_for_tests(True)
        render_script.generate_render_config_script(7, 'CYCLES', 'CPU', {})
        assert [r for r in caplog.records if r.name == LOGGER] == []
