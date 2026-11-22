# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The start-up rule (render per card FR-PC-SU, D13): ``try_reserve`` marks every claim *starting* just
before it returns it; under ``one_at_a_time`` check 0b declines an Unreal job while another claim is
starting, under ``together`` (the default) nothing waits; the setting ``unreal.startup_rule``.
"""
import logging

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import executor, reserve, startup_watch
from tests.unit.worker.unreal._executor_fakes import job
from tests.unit.worker.unreal.test_config_unreal import getter
from tests.unit.worker.unreal.test_reserve import make

ALL = tuple(range(16))


def _rule(value):
    return {'startup_rule': lambda: value}


class TestWatch:

    def test_mark_and_clear(self):
        watch = startup_watch.StartupWatch()
        watch.mark('a')
        watch.mark('b')
        assert watch.starting() == {'a', 'b'}
        assert watch.clear('a') is True and watch.clear('a') is False   # a second clear does nothing
        assert watch.starting() == {'b'}

    def test_clearing_an_unknown_claim_does_nothing(self):
        watch = startup_watch.StartupWatch()
        assert watch.clear('never-marked') is False and watch.starting() == frozenset()

    @pytest.mark.parametrize('rule, starting, admitted', [
        ('together', False, True), ('together', True, True),
        ('one_at_a_time', False, True), ('one_at_a_time', True, False),
    ])
    def test_admits(self, rule, starting, admitted):
        watch = startup_watch.StartupWatch()
        if starting:
            watch.mark('a')
        assert watch.admits(rule) is admitted


class TestMarkedInTryReserve:

    @pytest.mark.parametrize('rule', [None, 'together', 'one_at_a_time'])
    def test_the_reservation_is_marked_starting_under_its_claim_id(self, rule):
        reserver, _ = make(**(_rule(rule) if rule else {}))
        reservation = reserver.try_reserve(job())
        assert reserver.startup.starting() == {reservation.claim_id}
        assert reservation.startup is reserver.startup

    def test_together_marks_every_claim(self):
        reserver, _ = make(**_rule('together'))
        first, second = reserver.try_reserve(job(job_id=1)), reserver.try_reserve(job(job_id=2))
        assert reserver.startup.starting() == {first.claim_id, second.claim_id}

    def test_a_declined_job_is_never_marked(self):
        reserver, _ = make(isolation_ok=lambda: False)
        assert reserver.try_reserve(job()) is None and reserver.startup.starting() == frozenset()

    def test_a_failed_registration_marks_nothing_and_holds_nothing(self, monkeypatch):
        reserver, _ = make()
        monkeypatch.setattr(reserver.disk, 'register', lambda claim_id: (_ for _ in ()).throw(ValueError('dup')))
        with pytest.raises(ValueError):
            reserver.try_reserve(job())
        assert reserver.startup.starting() == frozenset()
        assert reserver.ram.snapshot() == {} and reserver.slots.free() == ALL

    def test_the_mark_comes_after_the_ledgers(self, monkeypatch):
        reserver, _ = make()
        seen = []
        monkeypatch.setattr(reserver.startup, 'mark',
                            lambda claim_id: seen.append((claim_id in reserver.ram.snapshot(), claim_id)))
        reservation = reserver.try_reserve(job())
        assert seen == [(True, reservation.claim_id)]

    def test_the_worker_uses_its_one_watch_and_the_setting(self, monkeypatch):
        deps = executor.default_reserve_deps()
        assert deps.startup is startup_watch.watch
        monkeypatch.setattr(config_unreal, 'STARTUP_RULE', 'one_at_a_time')
        assert deps.startup_rule() == 'one_at_a_time'


class TestTogether:

    @pytest.mark.parametrize('overrides', [{}, _rule('together')], ids=['default', 'together'])
    def test_a_second_claim_starts_while_the_first_is_starting(self, overrides):
        reserver, _ = make(**overrides)
        first = reserver.try_reserve(job(job_id=1))
        second = reserver.try_reserve(job(job_id=2))
        assert first is not None and second is not None and (first.slot, second.slot) == (0, 1)


class TestOneAtATime:

    def test_a_second_claim_is_declined_while_the_first_is_starting(self, caplog):
        reserver, reach = make(**_rule('one_at_a_time'))
        first = reserver.try_reserve(job(job_id=1))
        calls = len(reach.calls)
        with caplog.at_level(logging.WARNING, logger=reserve.__name__):
            assert reserver.try_reserve(job(job_id=2)) is None
        assert "Skipping Unreal job 2: another Unreal render on this machine is still starting." in caplog.text
        assert len(reach.calls) == calls                      # check 0b: before any share probe
        assert reserver.slots.free() == ALL[1:]               # the declined job holds nothing
        assert reserver.startup.starting() == {first.claim_id}

    def test_the_first_output_file_admits_the_next_claim(self):
        reserver, _ = make(**_rule('one_at_a_time'))
        first = reserver.try_reserve(job(job_id=1))
        first.startup_ended()
        second = reserver.try_reserve(job(job_id=2))
        assert second is not None and reserver.startup.starting() == {second.claim_id}

    def test_the_release_admits_the_next_claim(self):
        reserver, _ = make(**_rule('one_at_a_time'))
        reserver.try_reserve(job(job_id=1)).release()
        assert reserver.try_reserve(job(job_id=2)) is not None

    def test_retain_admits_the_next_claim_but_keeps_the_slot(self):
        reserver, _ = make(**_rule('one_at_a_time'))
        first = reserver.try_reserve(job(job_id=1))
        first.retain_until_restart()
        second = reserver.try_reserve(job(job_id=2))
        assert second is not None and second.slot == 1

    def test_the_rule_is_read_at_every_reservation(self):
        rule = {'value': 'together'}
        reserver, _ = make(startup_rule=lambda: rule['value'])
        reserver.try_reserve(job(job_id=1))
        rule['value'] = 'one_at_a_time'
        assert reserver.try_reserve(job(job_id=2)) is None


class TestSetting:

    def test_the_default_is_together(self):
        assert config_unreal.load(getter({}))['STARTUP_RULE'] == 'together'
        assert config_unreal.STARTUP_RULES == ('together', 'one_at_a_time')

    @pytest.mark.parametrize('raw, expected', [('one_at_a_time', 'one_at_a_time'), (' ONE_AT_A_TIME ', 'one_at_a_time'),
                                               ('together', 'together'), ('', 'together')])
    def test_values(self, raw, expected):
        assert config_unreal.load(getter({'startup_rule': raw}))['STARTUP_RULE'] == expected

    @pytest.mark.parametrize('raw', ['one-at-a-time', 'serial', 7])
    def test_an_invalid_value_warns_and_uses_the_default(self, raw, caplog):
        with caplog.at_level(logging.WARNING, logger=config_unreal.__name__):
            assert config_unreal.load(getter({'startup_rule': raw}))['STARTUP_RULE'] == 'together'
        assert 'unreal.startup_rule' in caplog.text

    def test_the_env_var_name(self, monkeypatch):
        monkeypatch.setenv('SETHLANS_UNREAL_STARTUP_RULE', 'one_at_a_time')
        assert config_unreal.load()['STARTUP_RULE'] == 'one_at_a_time'

    def test_the_example_config_names_it(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[4] / 'worker' / 'config.ini.example').read_text(encoding='utf-8')
        assert '# startup_rule = together' in text
