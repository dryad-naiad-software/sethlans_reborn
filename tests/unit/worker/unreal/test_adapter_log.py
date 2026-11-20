# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The check after launch (card selection FR-CU-3, B5, C5; AC-13).

``Chosen D3D12 Adapter Id = M`` with ``M != N`` (or a ``Found`` line with
another card's ids at ``N``) stops the run, raises ``adapter_mismatch``,
releases the job and refuses its project, also when ``add_alert`` raises;
Unreal is disabled until restart only when a second project mismatches
(security review: a project's ini can forge whole log lines). Only whole
lines count, so an ini value echoed inside another line cannot. A run that
ends without the line is a mismatch only when ``Found D3D12 adapter`` lines
are present and the worker did not stop it.
"""
import dataclasses
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError
from sethlans_worker_agent.unreal_executor import adapter_log, phases
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict, SurvivorsLeft
from sethlans_worker_agent.unreal_executor.prelaunch import PreLaunch
from sethlans_worker_agent.unreal_executor.zen_log import ZenLogResult
from tests.unit.worker.unreal._executor_fakes import outcome
from tests.unit.worker.unreal.test_process_runner import World

FOUND = '[x][  0]LogD3D12RHI: Found D3D12 adapter 1: AMD Radeon RX 7900 XT (VendorId: 1002, DeviceId: 744c)'
CHOSEN = '[x][  0]LogD3D12RHI: Chosen D3D12 Adapter Id = {}'
CHOICE = Choice(42, 'pci:0000:23:00.0', 'AMD Radeon RX 7900 XT', 1, 0x1002, 0x744C, None)
DETAIL = 'Unreal chose adapter 0 instead of 1'
PROJECT_A, PROJECT_B = 'a' * 64 + ':', 'a' * 64 + ':' + 'c' * 64   # one archive; without and with an overlay
ECHO = "[2026.11.19-10.00.00:000][  0]LogConfig: Set CVar [[r.Foo:{}]]"   # UE echoing a project ini value


def _watch():
    return adapter_log.AdapterWatch(1, adapter_log.ids_of(CHOICE))


class TestForgedLines:
    """Security review: ini values UE echoes inside another line, and absurd digit runs, are not adapter lines."""

    def test_an_echoed_chosen_line_is_ignored_and_the_real_one_counts(self):
        watch = _watch()
        assert watch.line(ECHO.format('LogD3D12RHI: Chosen D3D12 Adapter Id = 7')) is False
        assert watch.line(ECHO.format('LogD3D12RHI: Found D3D12 adapter 0: x (VendorId: 10de, DeviceId: 2b85')) is False
        assert watch.chosen is None and not watch.found
        assert watch.line(CHOSEN.format(1)) is False and watch.chosen == 1 and not watch.mismatch

    def test_an_echoed_found_line_does_not_make_an_unknown(self):
        watch = _watch()
        watch.line(ECHO.format('LogD3D12RHI: Found D3D12 adapter 0: x (VendorId: 10de'))
        result = dataclasses.replace(outcome(reason='exited'), adapter_found=watch.found)
        assert adapter_log.seen(result, 1) is None

    @pytest.mark.parametrize('line', [
        CHOSEN.format('9' * 5000), CHOSEN.format('123'), CHOSEN.format('1') + ' trailing', 'x' + CHOSEN.format(0),
        '[x][  0]LogD3D12RHI: Found D3D12 adapter ' + '9' * 5000 + ': x (VendorId: 1002, DeviceId: 744c',
    ])
    def test_unbounded_or_unanchored_lines_never_match_or_raise(self, line):
        watch = _watch()
        assert watch.line(line) is False and watch.chosen is None and not watch.found

    def test_the_pilot_reads_the_same_anchored_lines(self, tmp_path):
        from sethlans_worker_agent.unreal_executor import pilot
        log = tmp_path / 'render.1.log'
        log.write_text('\n'.join([ECHO.format('LogD3D12RHI: Chosen D3D12 Adapter Id = 0'), FOUND, CHOSEN.format(1)]),
                       encoding='utf-8')
        assert pilot.chosen_adapter(log) == 'AMD Radeon RX 7900 XT'


class TestIds:
    """Blender review: the Found line of the chosen index must name the chosen card's model."""

    def test_another_model_at_the_expected_index_is_a_mismatch(self):
        watch = _watch()
        watch.line('[x][  0]LogD3D12RHI: Found D3D12 adapter 1: AMD Radeon RX 6800 (VendorId: 1002, DeviceId: 73bf, '
                   'SubSysId: 0, Revision: 00c1')
        assert watch.line(CHOSEN.format(1)) is True
        assert watch.problem == '1 (PCI ids 1002:73bf, not the ticked card)'

    def test_revision_counts_only_when_both_sides_have_it(self):
        line = ('[x][  0]LogD3D12RHI: Found D3D12 adapter 1: AMD Radeon RX 7900 XT (VendorId: 1002, DeviceId: 744c, '
                'SubSysId: 0, Revision: 00c8')
        watch = _watch()
        watch.line(line)
        assert watch.line(CHOSEN.format(1)) is False                     # the choice has no revision
        watch = adapter_log.AdapterWatch(1, (0x1002, 0x744C, 0xCC))
        watch.line(line)
        assert watch.line(CHOSEN.format(1)) is True                      # an XTX swapped for the XT (C-17)

    def test_no_found_line_or_no_ids_is_not_judged(self):
        watch = adapter_log.AdapterWatch(1, None)
        watch.line(FOUND.replace('744c', '73bf'))
        assert watch.line(CHOSEN.format(1)) is False


class TestWatch:

    def test_the_chosen_line_against_the_expected_index(self):
        watch = adapter_log.AdapterWatch(1)
        assert watch.line(FOUND) is False and watch.found
        assert watch.line(CHOSEN.format(0)) is True and watch.chosen == 0 and watch.mismatch
        assert watch.line(CHOSEN.format(1)) is False and watch.chosen == 0   # the first line counts

    def test_the_expected_index_passes(self):
        watch = adapter_log.AdapterWatch(1)
        assert watch.line(CHOSEN.format(1)) is False and not watch.mismatch

    def test_a_launch_without_an_expected_index_reads_nothing(self):
        watch = adapter_log.AdapterWatch(None)
        assert watch.line(FOUND) is False and watch.line(CHOSEN.format(3)) is False
        assert watch.chosen is None and not watch.found


class TestRunnerStops:
    """The line monitor stops the process tree at the line (FR-CU-3)."""

    def test_another_adapter_stops_the_run(self, tmp_path):
        world = World(tmp_path)
        world.steps[2] = lambda: world.log(FOUND, CHOSEN.format(0))
        result = world.run(world.spec(expected_adapter=1))
        assert result.reason == 'adapter_mismatch' and 'terminate' in world.api.calls and not world.root.alive
        assert result.adapter_chosen == 0 and result.adapter_found and 'Chosen D3D12 Adapter Id = 0' in result.detail

    def test_the_expected_adapter_runs_on(self, tmp_path):
        world = World(tmp_path)
        world.steps[2] = lambda: world.log(FOUND, CHOSEN.format(1))
        world.steps[4] = lambda: setattr(world.root, 'alive', False)
        result = world.run(world.spec(expected_adapter=1))
        assert result.reason == 'exited' and result.adapter_chosen == 1

    def test_a_launch_without_adapter_ignores_the_lines(self, tmp_path):
        world = World(tmp_path)
        world.steps[2] = lambda: world.log(FOUND, CHOSEN.format(0))
        world.steps[3] = lambda: setattr(world.root, 'alive', False)
        result = world.run(world.spec())
        assert result.reason == 'exited' and result.adapter_chosen is None


class TestSeen:

    @pytest.mark.parametrize('chosen, found, reason, expected', [
        (0, True, 'exited', '0'), (0, True, 'shutdown', '0'), (0, False, 'adapter_mismatch', '0'),
        (1, True, 'exited', None),
        (None, True, 'exited', 'unknown'),
        (None, False, 'exited', None),           # no D3D12 lines at all: an ordinary failure
        (None, True, 'shutdown', None), (None, True, 'canceled', None), (None, True, 'timeout', None),
        (None, True, 'hang', None), (None, True, 'fatal', None), (None, True, 'exited_after_grace', None),
        (None, True, 'local_conflict', None),
    ])
    def test_table(self, chosen, found, reason, expected):
        result = dataclasses.replace(outcome(reason=reason), adapter_chosen=chosen, adapter_found=found)
        assert adapter_log.seen(result, 1) == expected

    def test_no_expected_index_is_never_a_mismatch(self):
        result = dataclasses.replace(outcome(), adapter_chosen=0, adapter_found=True)
        assert adapter_log.seen(result, None) is None


def _runner(run_outcome, add_alert, project=PROJECT_A):
    calls = {'disable': [], 'specs': []}
    config = phases.PhaseConfig(cache_dir='D:\\c', zen_port=8668, worker_sid=None, merged_names=['r.X'],
                                timeouts={'prep': 1, 'warmup': 2, 'render': 3}, hang_s=900, quit_grace_s=60,
                                log_max_bytes=10, log_flood_bytes_per_min=10)
    runner = phases.PhaseRunner(
        config=config, registry=object(), job_dir=SimpleNamespace(log=lambda p, n: f'{p}.log', stdout=lambda p, n: 's',
                                                                  ledger='l'),
        cancel=lambda: None, shutdown_event=None, sampler=None,
        run=lambda spec, registry: (calls['specs'].append(spec), run_outcome)[1],
        prelaunch=lambda *a, **k: PreLaunch(ok=True), verify_zen=lambda *a, **k: ZenLogResult(ok=True),
        loopback_factory=lambda *a, **k: None, disable_until_restart=calls['disable'].append, add_alert=add_alert,
        project=project)
    return runner, calls


def _alerts(error=None):
    raised = []

    def add_alert(kind, key, name, detail):
        raised.append((kind, key, name, detail))
        if error is not None:
            raise error
        return True
    return raised, add_alert


MISMATCH = dataclasses.replace(outcome(reason='adapter_mismatch'), adapter_chosen=0, adapter_found=True,
                               adapter_problem='0')


class TestAfterTheRun:

    @pytest.mark.parametrize('error', [None, ConfigLockTimeoutError('lock'), OSError('disk full'), RuntimeError('x')],
                             ids=['ok', 'lock', 'os', 'bug'])
    def test_mismatch_alerts_refuses_the_project_and_releases(self, error):
        raised, add_alert = _alerts(error)
        runner, calls = _runner(MISMATCH, add_alert)

        with pytest.raises(LocalConflict) as info:
            runner.launch('render', 1, SimpleNamespace(), adapter=CHOICE)

        spec = calls['specs'][0]
        assert (spec.expected_adapter, spec.expected_ids) == (1, (0x1002, 0x744C, None))
        assert info.value.reason == 'local_conflict'
        assert info.value.message == f'{DETAIL}; the job goes back to the queue.'
        assert raised == [('adapter_mismatch', CHOICE.key, CHOICE.name, f'{DETAIL}; project {'a' * 12}+none')]
        assert calls['disable'] == [] and adapter_log.refused(PROJECT_A) and not adapter_log.refused(PROJECT_B)

    def test_the_same_project_again_never_disables(self):
        for _ in range(3):
            runner, calls = _runner(MISMATCH, _alerts()[1])
            with pytest.raises(LocalConflict):
                runner.launch('render', 1, SimpleNamespace(), adapter=CHOICE)
            assert calls['disable'] == []

    def test_a_second_project_disables_unreal(self):
        runner_a, _calls = _runner(MISMATCH, _alerts()[1])
        with pytest.raises(LocalConflict):
            runner_a.launch('render', 1, SimpleNamespace(), adapter=CHOICE)
        runner_b, calls = _runner(MISMATCH, _alerts()[1], project=PROJECT_B)
        with pytest.raises(LocalConflict):
            runner_b.launch('warmup', 1, SimpleNamespace(), adapter=CHOICE)
        assert calls['disable'] == [adapter_log.DISABLED_REASON]
        assert len(adapter_log.DISABLED_REASON) <= 200

    def test_unknown_when_found_lines_but_no_chosen_line(self):
        raised, add_alert = _alerts()
        result = dataclasses.replace(outcome(reason='exited', exit_code=3), adapter_found=True)
        runner, calls = _runner(result, add_alert)
        with pytest.raises(LocalConflict, match='Unreal chose adapter unknown instead of 1'):
            runner.launch('warmup', 1, SimpleNamespace(), adapter=CHOICE)
        assert raised[0][3].startswith('Unreal chose adapter unknown instead of 1') and adapter_log.refused(PROJECT_A)

    @pytest.mark.parametrize('reason, found', [('exited', False), ('shutdown', True), ('timeout', True)])
    def test_no_alert_without_lines_or_after_the_workers_own_stop(self, reason, found):
        raised, add_alert = _alerts()
        runner, calls = _runner(dataclasses.replace(outcome(reason=reason), adapter_found=found), add_alert)
        try:
            runner.launch('render', 1, SimpleNamespace(), adapter=CHOICE)
        except Exception as exc:  # noqa: BLE001 - shutdown/timeout end as they always did
            assert not isinstance(exc, LocalConflict)
        assert raised == [] and calls['disable'] == [] and not adapter_log.refused(PROJECT_A)

    def test_survivors_still_alert_then_fail_as_survivors(self):
        raised, add_alert = _alerts()
        runner, calls = _runner(dataclasses.replace(MISMATCH, survivors=((5, 'x'),)), add_alert)
        with pytest.raises(SurvivorsLeft):
            runner.launch('render', 1, SimpleNamespace(), adapter=CHOICE)
        assert len(raised) == 1 and adapter_log.refused(PROJECT_A)

    def test_the_prep_has_no_adapter_check(self):
        raised, add_alert = _alerts()
        runner, calls = _runner(MISMATCH, add_alert)
        runner.launch('prep', 1, SimpleNamespace())
        assert calls['specs'][0].expected_adapter is None and raised == []

    def test_the_default_alert_goes_to_the_store(self, monkeypatch):
        from sethlans_worker_agent.cards import store
        raised = []
        monkeypatch.setattr(store, 'add_alert', lambda *args: raised.append(args) or True)
        adapter_log.report('0', CHOICE, PROJECT_A, lambda reason: None)
        assert raised == [('adapter_mismatch', CHOICE.key, CHOICE.name, f'{DETAIL}; project {'a' * 12}+none')]


class TestRefusedProject:
    """A refused project's jobs are declined by the reserver and refused by the job run before any launch."""

    def test_the_reserver_declines_it_and_holds_nothing(self):
        from tests.unit.worker.unreal._executor_fakes import job, params
        from tests.unit.worker.unreal.test_reserve import make, token_free
        adapter_log.report('0', CHOICE, adapter_log.project_key(params()), lambda reason: None, add_alert=lambda *a: True)
        reserver, reach = make()
        assert reserver.try_reserve(job()) is None and token_free(reserver) and reach.calls == []
        adapter_log.reset_for_tests()
        assert reserver.try_reserve(job()) is not None   # declined only because of the refusal

    def test_the_job_run_refuses_it_before_any_launch(self, tmp_path, monkeypatch):
        from tests.unit.worker.unreal._job_run_world import World
        w = World(tmp_path, monkeypatch)
        adapter_log.report('0', CHOICE, adapter_log.project_key(w.params), lambda reason: None, add_alert=lambda *a: True)
        w.flow_action = lambda job: pytest.fail('launched')
        result = w.run()
        assert result.released and ('release', 'local_conflict') in w.calls and 'finish' not in w.names()
