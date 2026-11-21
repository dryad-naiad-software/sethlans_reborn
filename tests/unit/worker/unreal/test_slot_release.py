# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The slot back in the pool on every path (render per card FR-PC-SLOT-1).

A slot taken by ``try_reserve`` comes back on every ``None`` or exception
path of the reservation, and, once the job ran, through the reservation's
release on every outcome (a prep exception, a cancel, a bug in the run, a
dispatch failure); a retained reservation (survivors) keeps it until restart.
"""
import logging
import threading

import pytest

from sethlans_worker_agent import job_processor, job_resources
from sethlans_worker_agent.executors.base import ExecutionContext
from sethlans_worker_agent.project_archive.errors import AbortedError, ShareUnavailableError
from sethlans_worker_agent.unreal_executor import executor, reserve, vram_gate_check, zen_port
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from tests.unit.worker.unreal._executor_fakes import job, reading
from tests.unit.worker.unreal._job_run_world import World, png_flow
from tests.unit.worker.unreal.test_reserve import Reach, Sampler, make

ALL = tuple(range(16))


def _raise(exc):
    raise exc


def _keyed():
    return reading(gpus=(GpuMem(0, 'a', 32607, 1000, None, None, 'pci:0000:01:00.0'),))


class _FreshRaises(Sampler):
    def fresh(self, max_age_s, deadline):
        raise RuntimeError('a bug in the fresh reading')


class TestReserveFailurePaths:
    """Paths of ``try_reserve`` after the slot is taken that ``test_slot_wiring`` does not name."""

    @pytest.mark.parametrize('kind, overrides, why', [
        ('render', dict(isolation_ok=lambda: False), 'known-folder check'),
        ('render', dict(resolve=lambda root, rel, kind: _raise(ValueError('outside'))), 'share path rejected'),
        ('render', dict(sampler=lambda: Sampler(reading(), fresh=None)), 'no usable GPU memory reading'),
        ('render', dict(reachability=lambda: Reach({'project-fixes': False})), 'not reachable on the share'),
        ('render', dict(sampler=lambda: Sampler(_keyed()), card_keys=lambda: ('pci:0000:99:00.0',)),
         vram_gate_check.NO_FREE_READING),  # the free card has no reading; card 01 is not in use (D4)
        ('pilot', {}, 'no PCI address'),
    ], ids=['isolation', 'share-path', 'no-fresh-reading', 'unreachable', 'card-not-in-use', 'pilot-no-key'])
    def test_a_decline_after_the_slot_gives_it_back(self, caplog, kind, overrides, why):
        reserver, _reach = make(**overrides)
        held = reserver.slots.take()
        with caplog.at_level(logging.WARNING, logger=reserve.__name__):
            assert reserver.try_reserve(job(job_id=2, kind=kind)) is None
        assert why in caplog.text
        assert held == 0 and reserver.slots.free() == ALL[1:]

    @pytest.mark.parametrize('overrides', [
        dict(port_ok=lambda slot: _raise(RuntimeError('a bug in check 6a'))),
        dict(isolation_ok=lambda: _raise(RuntimeError('a bug in check 6b'))),
        dict(share_state=lambda: _raise(RuntimeError('a bug in the share state'))),
        dict(sampler=lambda: _FreshRaises(reading())),
    ], ids=['port', 'isolation', 'share', 'fresh-gate'])
    def test_an_exception_after_the_slot_gives_it_back(self, overrides):
        reserver, _reach = make(**overrides)
        with pytest.raises(RuntimeError):
            reserver.try_reserve(job())
        assert reserver.slots.free() == ALL

    def test_the_slot_given_back_is_the_one_taken(self):
        """A failing reservation on slot 1 never frees slot 0 of the running job."""
        on = {'fail': False}
        reserver, _reach = make(port_ok=lambda slot: zen_port.PortCheck(ok=not (on['fail'] and slot == 1),
                                                                        error='x'))
        first = reserver.try_reserve(job(job_id=1))
        on['fail'] = True
        assert reserver.try_reserve(job(job_id=2)) is None
        assert first.slot == 0 and reserver.slots.free() == ALL[1:]
        on['fail'] = False
        assert reserver.try_reserve(job(job_id=3)).slot == 1


class _Run:
    """A real ``Reserver`` reservation on slot 1 (slot 0 held by another job), run by ``World``'s fakes."""

    def __init__(self, tmp_path, monkeypatch):
        self.reserver, _reach = make()
        self.other = self.reserver.try_reserve(job(job_id=1))
        self.world = World(tmp_path, monkeypatch)
        self.world.flow_action = png_flow
        self.reservation = self.reserver.try_reserve(job(job_id=self.world.params.job_id))
        assert (self.other.slot, self.reservation.slot) == (0, 1)
        self.reservation.params = self.world.params
        self.reservation.gate = vram_gate_check.evaluate(self.world.params, self.world.reading)
        self.job_data = {'id': self.world.params.job_id, '_renderer_reservation': self.reservation}

    def execute(self):
        ex = executor.UnrealExecutor(reserve_deps=executor.default_reserve_deps(), run_deps=self.world.deps())
        return ex.execute(self.job_data, ExecutionContext(None, None, None, threading.Event()))

    def free(self):
        return self.reserver.slots.free()


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(job_processor, 'get_worker_capacity', lambda: None)
    return _Run(tmp_path, monkeypatch)


def _prep_bug(world):
    world.share_error = RuntimeError('a bug in the workspace prep')


def _prep_share(world):
    world.share_error = ShareUnavailableError('The archive is not reachable on the Sethlans share.')


def _prep_abort(world):
    world.share_error = AbortedError('canceled')


def _cancel(world):
    world.flow_action = lambda job_: _raise(Cancelled('canceled'))


def _flow_bug(world):
    world.flow_action = lambda job_: _raise(RuntimeError('a bug in the flow'))


class TestRunPaths:

    @pytest.mark.parametrize('setup, outcome', [
        (None, lambda r, w: r.success and 'upload' in w.names()),
        (_prep_bug, lambda r, w: 'unexpectedly' in r.error_message),
        (_prep_share, lambda r, w: 'unreachable' in w.names()),
        (_prep_abort, lambda r, w: r.was_canceled),
        (_cancel, lambda r, w: r.was_canceled),
        (_flow_bug, lambda r, w: 'unexpectedly' in r.error_message),
    ], ids=['success', 'prep-exception', 'prep-share', 'prep-cancel', 'cancel', 'flow-exception'])
    def test_the_release_after_any_outcome_gives_the_slot_back(self, run, setup, outcome):
        if setup is not None:
            setup(run.world)
        assert outcome(run.execute(), run.world)
        assert run.free() == ALL[2:]  # the run itself never gives the slot back
        assert job_resources.release_reservations(run.world.params.job_id, run.reservation, None) is True
        assert run.free() == (1,) + ALL[2:]
        run.other.release()
        assert run.free() == ALL

    def test_survivors_retain_the_slot_until_restart(self, run):
        run.world.survivors = ((5, 'UnrealEditor-Cmd.exe'),)
        run.execute()
        assert run.reservation.retained
        assert job_resources.release_reservations(run.world.params.job_id, run.reservation, None) is False
        job_resources.release_after_job(run.job_data)  # the lifecycle's safety net keeps it as well
        run.other.release()
        assert run.free() == (0,) + ALL[2:]
        assert run.reserver.try_reserve(job(job_id=7)).slot == 0

    def test_retain_before_release_keeps_the_slot_even_after_a_cancel(self, run):
        _cancel(run.world)
        run.world.survivors = ((5, 'UnrealEditor-Cmd.exe'),)
        run.execute()
        assert not run.reservation.release()
        assert 1 not in run.free()

    def test_an_exception_out_of_the_run_is_given_back_by_the_safety_net(self, run):
        registry = run.world.registry

        def broken_registry():
            reg = registry()
            reg.confirm_all = lambda: _raise(RuntimeError('a bug in the exit confirmation'))
            return reg
        run.world.registry = broken_registry
        with pytest.raises(RuntimeError):
            run.execute()
        assert 1 not in run.free()
        job_resources.release_after_job(run.job_data)
        assert run.free() == (1,) + ALL[2:]

    def test_a_dispatch_failure_gives_the_slot_back(self, run):
        job_resources.release_after_dispatch_failure(run.job_data, None)
        assert run.free() == (1,) + ALL[2:]
        job_resources.release_after_job(run.job_data)  # already released: given back once, no ValueError
        assert run.free() == (1,) + ALL[2:]
