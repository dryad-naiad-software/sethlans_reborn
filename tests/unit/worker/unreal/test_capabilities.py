# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Capabilities report, poll parameters and startup checks (Unreal foundation
FR-WD-6, FR-WD-7, FR-WD-9, FR-WC-6/7, NF-6, AC-9, AC-21, AC-33;
FR-T-5 "config_unreal", "Share" and "Heartbeat payload").
"""
import os
import subprocess
import sys
import types

import pytest

from sethlans_worker_agent import config_unreal, unreal_share
from sethlans_worker_agent.project_archive import owner_lock
from sethlans_worker_agent.unreal_executor import cache_dir, capabilities, detection, runtime, symlink_policy
from shared import owner_only_acl
from tests._unreal_worker_state import (  # noqa: F401
    BUILD_57, BUILD_58, FakeSampler, enable_unreal, install, reachable, reading, unreal_runtime_reset,
)

pytestmark = pytest.mark.usefixtures('unreal_runtime_reset')

SHARE = r'\\nas.lan\sethlans'
DISABLED = {'schema': 1, 'enabled': False, 'disabled_reason': 'disabled by config',
            'installs': [], 'share': '', 'share_reachable': False}


def _boom(*args, **kwargs):
    raise AssertionError('Unreal work while disabled')


class TestDisabledByConfig:

    def test_short_circuit_touches_nothing(self, monkeypatch):
        monkeypatch.setattr(config_unreal, 'ENABLED', False)
        for target, name in [(runtime, 'startup'), (detection, 'detect_engines'),
                             (cache_dir, 'prepare_cache_dir'), (owner_lock, 'acquire_for_process'),
                             (owner_only_acl, 'apply_owner_only'), (owner_only_acl, 'verify_owner_only'),
                             (unreal_share, 'configure'), (unreal_share, 'state'),
                             (subprocess, 'run'), (os, 'makedirs'), (os, 'mkdir'), (os, 'stat')]:
            monkeypatch.setattr(target, name, _boom)
        capabilities.startup()
        assert capabilities.snapshot() == DISABLED
        assert capabilities.poll_params(node_idle=True) == {}
        assert capabilities.gpu_memory_payload() is None

    def test_no_threads_started(self, monkeypatch):
        import threading
        monkeypatch.setattr(config_unreal, 'ENABLED', False)
        before = {t.name for t in threading.enumerate()}
        capabilities.snapshot()
        capabilities.poll_params(node_idle=True)
        after = {t.name for t in threading.enumerate()}
        assert not {name for name in after - before if name.startswith('unreal-')}


class TestEnabled:

    def test_enabled_report(self, monkeypatch):
        enable_unreal(monkeypatch, installs=(install(BUILD_57, False, reason='UE 5.7 not supported (R14)'),
                                             install()),
                      share=r'\\nas.lan\sethlans', share_state=reachable())
        snap = capabilities.snapshot()
        assert snap == {
            'schema': 1, 'enabled': True, 'disabled_reason': '',
            'installs': [{'build': BUILD_57, 'supported': False, 'source': 'launcher'},
                         {'build': BUILD_58, 'supported': True, 'source': 'launcher'}],
            'share': r'\\nas.lan\sethlans', 'share_reachable': True,
        }

    def test_startup_runs_once(self, monkeypatch):
        calls = enable_unreal(monkeypatch)
        for _ in range(3):
            capabilities.snapshot()
            capabilities.poll_params(node_idle=True)
        assert calls['prepare'] == 1 and calls['lock'] == 1 and calls['threads'] == 1

    def test_installs_without_build_are_not_reported(self, monkeypatch):
        enable_unreal(monkeypatch, installs=(install(None, False, reason='unreadable'), install()))
        assert [i['build'] for i in capabilities.snapshot()['installs']] == [BUILD_58]

    def test_rescan_after_interval(self, monkeypatch):
        calls = enable_unreal(monkeypatch)
        clock = types.SimpleNamespace(now=0.0)
        monkeypatch.setattr(runtime, '_clock', lambda: clock.now)
        capabilities.snapshot()
        capabilities.snapshot()
        assert calls['detect'] == 1
        clock.now += config_unreal.RESCAN_SECONDS
        capabilities.snapshot()
        assert calls['detect'] == 2

    def test_share_and_reachability_come_from_one_read(self, monkeypatch):
        enable_unreal(monkeypatch)
        reads = []
        states = iter([reachable(), reachable(ok=False)])
        monkeypatch.setattr(unreal_share, 'state', lambda: reads.append(1) or next(states))
        snap = capabilities.snapshot()
        assert len(reads) == 1 and snap['share_reachable'] is True


class TestDisabledReasons:

    @pytest.mark.parametrize('kwargs,reason', [
        ({'force_gpu_index': '0'}, runtime.FORCE_GPU_INDEX_REASON),
        ({'symlink_eval': True}, runtime.REMOTE_SYMLINK_REASON),
        ({'share': r'\\nas\C$'}, 'invalid SETHLANS_UNREAL_SHARE: '),
        ({'share': r'D:\share'}, 'invalid SETHLANS_UNREAL_SHARE: '),
        ({'cache_reason': 'invalid SETHLANS_UNREAL_CACHE_DIR, it is inside a OneDrive folder'}, 'OneDrive'),
        ({'cache_reason': cache_dir.ACL_REASON}, cache_dir.ACL_REASON),
        ({'lock_ok': False}, owner_lock.OWNER_LOCK_REASON),
        ({'installs': ()}, runtime.NO_INSTALL_REASON),
        ({'installs': (install(BUILD_57, False),)}, runtime.NO_INSTALL_REASON),
    ])
    def test_reason(self, monkeypatch, kwargs, reason):
        calls = enable_unreal(monkeypatch, **kwargs)
        snap = capabilities.snapshot()
        assert snap['enabled'] is False and reason in snap['disabled_reason']
        assert capabilities.poll_params(node_idle=True) == {}
        if 'installs' not in kwargs:
            assert calls['threads'] == 0
            assert capabilities.gpu_memory_payload() is None

    def test_force_gpu_index_reason_text(self, monkeypatch):
        enable_unreal(monkeypatch, force_gpu_index='1')
        assert capabilities.snapshot()['disabled_reason'] == (
            "SETHLANS_FORCE_GPU_INDEX is set: Unreal needs the whole machine (phase 1)")

    def test_owner_lock_failure_skips_nothing_else_after_it(self, monkeypatch):
        calls = enable_unreal(monkeypatch, lock_ok=False)
        capabilities.snapshot()
        assert calls['prepare'] == 1 and calls['threads'] == 0

    def test_empty_share_does_not_disable(self, monkeypatch):
        enable_unreal(monkeypatch, share='')
        snap = capabilities.snapshot()
        assert snap['enabled'] is True and snap['share'] == '' and snap['share_reachable'] is False
        assert capabilities.poll_params(node_idle=True) == {}

    def test_disable_until_restart(self, monkeypatch):
        enable_unreal(monkeypatch)
        capabilities.snapshot()
        runtime.disable_until_restart('orphaned Unreal processes survived termination')
        snap = capabilities.snapshot()
        assert snap['enabled'] is False and 'orphaned' in snap['disabled_reason']

    def test_startup_exception_fails_closed(self, monkeypatch):
        enable_unreal(monkeypatch)

        def broken():
            raise RuntimeError('boom')

        monkeypatch.setattr(runtime, '_check_platform_and_config', broken)
        snap = capabilities.snapshot()
        assert snap['enabled'] is False and 'RuntimeError' in snap['disabled_reason']


class TestRemoteSymlinkPolicy:
    """``symlink_policy`` with a fake ``winreg`` (Sec N5; fail closed)."""

    FS_KEY = symlink_policy.KEYS[0]
    POLICY_KEY = symlink_policy.KEYS[1]

    def _fake_winreg(self, keys):
        """``keys``: {key path: {value name: value} | Exception}; absent key = FileNotFoundError."""
        class Key:
            def __init__(self, values):
                self.values = values

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def open_key(root, path):
            entry = keys.get(path, FileNotFoundError(path))
            if isinstance(entry, Exception):
                raise entry
            return Key(entry)

        def query(key, name):
            value = key.values.get(name, FileNotFoundError(name))
            if isinstance(value, Exception):
                raise value
            return value, 4

        return types.SimpleNamespace(HKEY_LOCAL_MACHINE=object(), OpenKey=open_key, QueryValueEx=query)

    def _enabled(self, monkeypatch, keys):
        monkeypatch.setitem(sys.modules, 'winreg', self._fake_winreg(keys))
        monkeypatch.setattr(symlink_policy, '_is_windows', lambda: True)
        return symlink_policy.remote_symlink_evaluation_enabled()

    @pytest.mark.parametrize('values,expected', [
        ({}, False),
        ({'SymlinkRemoteToRemoteEvaluation': 0, 'SymlinkRemoteToLocalEvaluation': 0}, False),
        ({'SymlinkRemoteToRemoteEvaluation': 1}, True),
        ({'SymlinkRemoteToLocalEvaluation': 1}, True),
        ({'SymlinkRemoteToRemoteEvaluation': 2}, True),
        ({'SymlinkRemoteToRemoteEvaluation': -1}, True),
        ({'SymlinkRemoteToRemoteEvaluation': '0'}, True),
        ({'SymlinkRemoteToRemoteEvaluation': b'\x00'}, True),
        ({'SymlinkLocalToRemoteEvaluation': 1}, False),
    ])
    def test_values(self, monkeypatch, values, expected):
        assert self._enabled(monkeypatch, {self.FS_KEY: values}) is expected

    def test_missing_keys_mean_default(self, monkeypatch):
        assert self._enabled(monkeypatch, {}) is False

    def test_policy_key_is_read_too(self, monkeypatch):
        keys = {self.FS_KEY: {}, self.POLICY_KEY: {'SymlinkRemoteToLocalEvaluation': 1}}
        assert self._enabled(monkeypatch, keys) is True

    @pytest.mark.parametrize('broken', ['key', 'value'])
    def test_access_denied_propagates(self, monkeypatch, broken):
        denied = PermissionError(5, 'Access is denied')
        keys = {self.FS_KEY: denied} if broken == 'key' else {
            self.FS_KEY: {'SymlinkRemoteToRemoteEvaluation': denied}}
        with pytest.raises(OSError):
            self._enabled(monkeypatch, keys)

    def test_unreadable_policy_disables_unreal(self, monkeypatch):
        enable_unreal(monkeypatch)

        def denied():
            raise PermissionError(5, 'Access is denied')

        monkeypatch.setattr(symlink_policy, 'remote_symlink_evaluation_enabled', denied)
        snap = capabilities.snapshot()
        assert snap['enabled'] is False and snap['disabled_reason'] == runtime.REMOTE_SYMLINK_REASON

    def test_not_windows(self, monkeypatch):
        monkeypatch.setattr(symlink_policy, '_is_windows', lambda: False)
        assert symlink_policy.remote_symlink_evaluation_enabled() is False


class TestVramReason:

    def test_no_reading_ever_disables_and_omits_unreal(self, monkeypatch):
        enable_unreal(monkeypatch, share=SHARE, share_state=reachable(),
                      sampler=FakeSampler(None, never_succeeded=True))
        snap = capabilities.snapshot()
        assert snap['enabled'] is False and snap['disabled_reason'] == runtime.NO_VRAM_REASON
        assert capabilities.poll_params(node_idle=True) == {}

    def test_an_earlier_success_keeps_unreal_enabled(self, monkeypatch):
        enable_unreal(monkeypatch, share=SHARE, share_state=reachable(),
                      sampler=FakeSampler(None, never_succeeded=False))
        assert capabilities.snapshot()['enabled'] is True
        assert 'renderers' in capabilities.poll_params(node_idle=True)


class TestPollParams:

    def test_idle_reachable_and_supported(self, monkeypatch):
        enable_unreal(monkeypatch, installs=(install(), install('5.8.1-1000', source='config'),
                                             install(BUILD_57, False)),
                      share=r'\\nas.lan\sethlans', share_state=reachable())
        assert capabilities.poll_params(node_idle=True) == {
            'renderers': 'BLENDER,UNREAL', 'unreal_builds': f'5.8.1-1000,{BUILD_58}'}

    @pytest.mark.parametrize('node_idle,state', [
        (False, reachable()), (True, reachable(ok=False)), (True, unreal_share.NOT_CONFIGURED),
    ])
    def test_omitted(self, monkeypatch, node_idle, state):
        enable_unreal(monkeypatch, share=r'\\nas.lan\sethlans', share_state=state)
        assert capabilities.poll_params(node_idle=node_idle) == {}

    def test_at_most_ten_builds(self, monkeypatch):
        many = tuple(install(f'5.8.{n}-{100 + n}') for n in range(12))
        enable_unreal(monkeypatch, installs=many, share=r'\\nas.lan\s', share_state=reachable())
        assert len(capabilities.poll_params(node_idle=True)['unreal_builds'].split(',')) == 10


class TestGpuMemoryPayload:

    def test_from_the_sampler_reading(self, monkeypatch):
        enable_unreal(monkeypatch, sampler=FakeSampler(reading(taken_at=0.0)))
        monkeypatch.setattr('time.monotonic', lambda: 4.0)
        payload = capabilities.gpu_memory_payload()
        assert payload == {'schema': 1, 'source': 'nvidia-smi', 'age_s': 4, 'gpus': [
            {'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607, 'used_mb': 1012,
             'sethlans_used_mb': None, 'other_used_mb': None}]}

    def test_no_reading_yet(self, monkeypatch):
        enable_unreal(monkeypatch, sampler=FakeSampler(None))
        assert capabilities.gpu_memory_payload() is None
