# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Executor registry, Blender adapter and RendererReservation
(Unreal foundation FR-WX-1, FR-WX-2, FR-WX-3, FR-T-5 "Executors").
"""
import sys
import threading
import types

import pytest

from sethlans_worker_agent import executors
from sethlans_worker_agent.executors import (
    ExecutionContext,
    RendererReservation,
    UnsupportedRendererError,
)
from sethlans_worker_agent.executors.blender import BlenderExecutor

from ._fakes import UNREAL_MODULE, fail_imports

BLENDER_TUPLE = (True, False, True, 'Time: 00:01.50', 'err', 'msg',
                 '/out/f.png', '/out/t.png', 'monitor', 'finished')


def _ctx():
    return ExecutionContext(
        assigned_gpu_index=1, yield_monitor_factory=lambda pid: None,
        manual_stop_event=threading.Event(), shutdown_event=threading.Event(),
    )


class TestRegistry:

    def test_blender_lookup(self):
        executor = executors.get_executor('BLENDER')
        assert isinstance(executor, BlenderExecutor)
        assert executor.capacity_mode == 'shared'
        assert executor.renderer == 'BLENDER'

    @pytest.mark.parametrize('renderer', ['MAYA', '', 'blender', 3])
    def test_unknown_renderer_raises_with_report_text(self, renderer):
        with pytest.raises(UnsupportedRendererError) as info:
            executors.get_executor(renderer)
        assert str(info.value) == f"Unsupported renderer '{renderer}' on this worker."

    def test_real_unreal_executor_loads_once_without_threads(self):
        """Chunk 7b: the real executor loads lazily; its __init__ starts no thread (chunk 3 L3)."""
        before = {t.ident for t in threading.enumerate()}
        executor = executors.get_executor('UNREAL')
        assert type(executor).__module__ == UNREAL_MODULE
        assert executor.renderer == 'UNREAL' and executor.capacity_mode == 'exclusive'
        assert executors.get_executor('UNREAL') is executor
        assert {t.ident for t in threading.enumerate()} <= before

    def test_unreal_import_failure_raises_unsupported(self, monkeypatch, caplog):
        caplog.set_level('ERROR', logger='sethlans_worker_agent.executors')
        monkeypatch.delitem(sys.modules, UNREAL_MODULE, raising=False)
        fail_imports(monkeypatch, UNREAL_MODULE)
        for _ in range(3):
            with pytest.raises(UnsupportedRendererError) as info:
                executors.get_executor('UNREAL')
            assert str(info.value) == "Unsupported renderer 'UNREAL' on this worker."
        assert len([r for r in caplog.records if r.levelname == 'ERROR']) == 1

    def test_failed_import_is_cached_not_retried(self, monkeypatch):
        monkeypatch.delitem(sys.modules, UNREAL_MODULE, raising=False)
        with monkeypatch.context() as patch:
            fail_imports(patch, UNREAL_MODULE)
            with pytest.raises(UnsupportedRendererError):
                executors.get_executor('UNREAL')
        assert executors._unreal_load_error is not None
        # Even if the module became importable, the cached failure stands
        # until restart: no second import attempt.
        module = types.ModuleType(UNREAL_MODULE)
        module.UnrealExecutor = lambda: pytest.fail('imported again')
        monkeypatch.setitem(sys.modules, UNREAL_MODULE, module)
        with pytest.raises(UnsupportedRendererError):
            executors.get_executor('UNREAL')

    def test_unreal_is_lazy_singleton(self, fake_unreal):
        assert executors._unreal_executor is None
        assert executors.get_executor('UNREAL') is fake_unreal
        assert executors.get_executor('UNREAL') is fake_unreal

    def test_unreal_executor_not_imported_by_blender_paths(self, monkeypatch):
        monkeypatch.delitem(sys.modules, UNREAL_MODULE, raising=False)
        executors.get_executor('BLENDER')
        assert UNREAL_MODULE not in sys.modules

    @pytest.mark.parametrize('renderer,expected', [
        ('UNREAL', True), ('BLENDER', False), (None, False), ('', False),
    ])
    def test_supports_release(self, renderer, expected):
        assert executors.supports_release(renderer) is expected


class TestBlenderExecutor:

    def test_try_reserve_is_noop_reservation(self):
        reservation = BlenderExecutor().try_reserve({'id': 1})
        assert isinstance(reservation, RendererReservation)
        reservation.release()
        reservation.release()
        assert reservation.released is True

    def test_execute_maps_tuple_one_to_one(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=BLENDER_TUPLE,
        )
        result = BlenderExecutor().execute({'id': 1}, _ctx())
        assert (result.success, result.was_canceled, result.was_yielded,
                result.stdout, result.stderr, result.error_message,
                result.output_path, result.thumbnail_path,
                result.yield_monitor, result.grace_outcome) == BLENDER_TUPLE
        assert result.render_time_seconds is None
        assert result.released is False

    def test_patching_module_attribute_reaches_adapter(self, mocker):
        """FR-WX-2: the lookup happens at call time, on the module."""
        ctx = _ctx()
        spy = mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=BLENDER_TUPLE,
        )
        job = {'id': 5}
        executors.get_executor('BLENDER').execute(job, ctx)
        spy.assert_called_once_with(
            job, assigned_gpu_index=1,
            yield_monitor_factory=ctx.yield_monitor_factory,
            manual_stop_event=ctx.manual_stop_event,
            shutdown_event=ctx.shutdown_event,
        )


class TestRendererReservation:

    def test_release_runs_callback_once(self):
        calls = []
        reservation = RendererReservation(on_release=lambda: calls.append(1))
        assert reservation.release() is True
        assert reservation.release() is True
        assert calls == [1]

    def test_retained_reservation_never_releases(self):
        calls = []
        reservation = RendererReservation(on_release=lambda: calls.append(1))
        reservation.retain_until_restart()
        assert reservation.release() is False
        assert reservation.retained is True
        assert reservation.released is False
        assert calls == []

    def test_retain_after_release_is_ignored(self):
        reservation = RendererReservation()
        reservation.release()
        reservation.retain_until_restart()
        assert reservation.retained is False

    def test_callback_error_is_swallowed(self):
        def boom():
            raise RuntimeError('token gone')
        reservation = RendererReservation(on_release=boom)
        reservation.release()  # must not raise
        assert reservation.released is True
