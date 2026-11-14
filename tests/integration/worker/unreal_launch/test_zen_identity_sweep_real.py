# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Farm-zenserver identity, port check and orphan sweep against real processes (FR-UE-23, FR-UE-26; AC-52).

The "zenserver" is a copy of the base Python interpreter named ``zenserver.exe``; no Unreal process is started
and nothing outside ``tmp_path`` is touched.
"""
import json
import os
import socket
import subprocess
import sys

import psutil
import pytest

from sethlans_worker_agent.unreal_executor import orphans, win32_proc, zen_port, zen_private
from shared import owner_only_acl

from ._children import SLEEP, alive, fake_zenserver, kill_quietly, python_args, start_zenserver, wait_until

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows process identity')


@pytest.fixture(scope='module')
def zen_exe(tmp_path_factory):
    return fake_zenserver(tmp_path_factory.mktemp('zenbin'))


@pytest.fixture()
def cache(tmp_path):
    path = tmp_path / 'uecache'
    path.mkdir()
    return path


def _ready(proc):
    assert wait_until(lambda: '--data-dir' in ' '.join(psutil.Process(proc.pid).cmdline()))
    return psutil.Process(proc.pid)


def test_owner_sid_of_a_child_is_the_workers():
    child = subprocess.Popen(python_args(SLEEP.format(seconds=30)), creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert win32_proc.process_owner_sid(child.pid) == owner_only_acl.current_user_sid()
    finally:
        kill_quietly(child)


def test_identity_table(cache, zen_exe, monkeypatch):
    farm = start_zenserver(zen_exe, zen_private.zen_data_dir(cache))
    other_dir = start_zenserver(zen_exe, str(cache) + '2\\zen\\data')
    below = start_zenserver(zen_exe, zen_private.zen_data_dir(cache) + '\\x')
    try:
        assert zen_private.is_farm_zenserver(_ready(farm), cache)
        assert not zen_private.is_farm_zenserver(_ready(other_dir), cache)
        assert not zen_private.is_farm_zenserver(_ready(below), cache)
        monkeypatch.setattr(zen_private.win32_proc, 'process_owner_sid', lambda pid: 'S-1-5-21-9-9-9-500')
        assert not zen_private.is_farm_zenserver(_ready(farm), cache)  # another owner
    finally:
        kill_quietly(farm, other_dir, below)


def test_a_python_process_with_the_right_data_dir_is_not_a_zenserver(cache):
    impostor = subprocess.Popen(python_args(SLEEP.format(seconds=30)) + ['--data-dir', zen_private.zen_data_dir(cache)],
                                creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert not zen_private.is_farm_zenserver(_ready(impostor), cache)
    finally:
        kill_quietly(impostor)


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _listen_code(port):
    return (f"import socket, time; s = socket.socket(); s.bind(('127.0.0.1', {port})); s.listen(); "
            "time.sleep(60)")


def test_port_check_against_real_listeners(cache, zen_exe):
    port = _free_port()
    assert zen_port.check_port(port, cache).ok  # nothing listens
    farm = start_zenserver(zen_exe, zen_private.zen_data_dir(cache), code=_listen_code(port))
    try:
        assert wait_until(lambda: any(item.pid == farm.pid for item in zen_port.listeners(port)))
        result = zen_port.check_port(port, cache)
        assert result.ok and [item.pid for item in result.farm] == [farm.pid]
        loop = zen_port.check_loopback(port, cache)
        assert loop.seen and loop.ok and loop.addresses == ('127.0.0.1',)
    finally:
        kill_quietly(farm)
    foreign = subprocess.Popen(python_args(_listen_code(port)), creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert wait_until(lambda: bool(zen_port.listeners(port)))
        result = zen_port.check_port(port, cache)
        assert not result.ok and result.foreign[0].address == '127.0.0.1'
    finally:
        kill_quietly(foreign)


def test_sweep_kills_ledger_entries_and_the_farm_zenserver_only(cache, zen_exe):
    # Ledger entries are trusted only for images under an engine root or CACHE_DIR (Sec S6), so the
    # listed children run from a copy of the interpreter inside the cache.
    child_exe = fake_zenserver(cache / 'ueprofile' / 'bin')
    os.replace(child_exe, child_exe.with_name('CrashReportClientEditor.exe'))
    child_exe = child_exe.with_name('CrashReportClientEditor.exe')
    env = dict(os.environ, PYTHONHOME=sys.base_prefix)
    farm = start_zenserver(zen_exe, zen_private.zen_data_dir(cache))
    other_zen = start_zenserver(zen_exe, str(cache) + '2\\zen\\data')
    listed = subprocess.Popen([str(child_exe), '-c', SLEEP.format(seconds=60)], env=env,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    reused = subprocess.Popen([str(child_exe), '-c', SLEEP.format(seconds=60)], env=env,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    outside = subprocess.Popen(python_args(SLEEP.format(seconds=60)), creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        for proc in (farm, other_zen):
            _ready(proc)
        entries = []
        for proc, shift in ((listed, 0.0), (reused, 5.0), (outside, 0.0)):  # 'reused': create_time differs
            ps = psutil.Process(proc.pid)
            entries.append({'pid': proc.pid, 'create_time': ps.create_time() - shift, 'exe': ps.exe()})
        job_dir = cache / 'j' / '42-1a2b3c4d'
        job_dir.mkdir(parents=True)
        (job_dir / 'procs.json').write_text(json.dumps({'schema': 1, 'procs': entries}))
        result = orphans.sweep(cache, engine_roots=[])
        killed = {pid for pid, _exe in result.killed}
        assert farm.pid in killed and listed.pid in killed and result.survivors == ()
        assert other_zen.pid not in killed and reused.pid not in killed and outside.pid not in killed
        assert wait_until(lambda: not alive(farm.pid) and not alive(listed.pid))
        assert alive(other_zen.pid) and alive(reused.pid) and alive(outside.pid)
    finally:
        kill_quietly(farm, other_zen, listed, reused, outside)


def test_sweep_name_rule_needs_engine_root_and_cache_reference(cache, tmp_path):
    engine = tmp_path / 'UE_5.8'
    binaries = engine / 'Engine' / 'Binaries' / 'Win64'
    zen_like = fake_zenserver(binaries)
    os.replace(zen_like, binaries / 'ShaderCompileWorker.exe')
    env = dict(os.environ, PYTHONHOME=sys.base_prefix)
    scw = subprocess.Popen([str(binaries / 'ShaderCompileWorker.exe'), '-c', SLEEP.format(seconds=60), str(cache)],
                           env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    unrelated = subprocess.Popen([str(binaries / 'ShaderCompileWorker.exe'), '-c', SLEEP.format(seconds=60)],
                                 env=env, cwd=str(tmp_path), creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert wait_until(lambda: str(cache) in ' '.join(psutil.Process(scw.pid).cmdline()))
        result = orphans.sweep(cache, engine_roots=[engine])
        assert {pid for pid, _exe in result.killed} == {scw.pid}
        assert alive(unrelated.pid)
    finally:
        kill_quietly(scw, unrelated)
