# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal off keeps the Windows GPU memory backend out of the worker (GPU memory reader AC-7, FR-GU-8, NF-6).

A fresh interpreter runs the worker's real start path: ``agent.main()`` (with ``unreal_bridge.startup()``),
the registration heartbeat, one periodic heartbeat and the Unreal poll parameters. Only the parts that are not
Unreal's are stubbed: the setup phase (TLS, web server, wizard), the session monitor, logging setup, hardware
and card reports, and the manager (the heartbeat is captured, never sent). ``ctypes`` library loads and
subprocess launches are recorded from the first line, so a ``pdh.dll`` load or an ``nvidia-smi`` run by any
Sethlans code shows up. Runs on every OS; the data directory is a temporary one.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]

_CHILD = r"""
import ctypes, json, subprocess, sys, types

loads, spawns, beats = [], [], []
_cdll_init = ctypes.CDLL.__init__
_popen_init = subprocess.Popen.__init__


def record_load(self, name, *args, **kwargs):
    loads.append(str(name))
    _cdll_init(self, name, *args, **kwargs)


def record_spawn(self, args, *rest, **kwargs):
    spawns.append(args if isinstance(args, str) else ' '.join(str(arg) for arg in args))
    _popen_init(self, args, *rest, **kwargs)


ctypes.CDLL.__init__ = record_load        # WinDLL and windll.<name> come through here
subprocess.Popen.__init__ = record_spawn

session = types.ModuleType('sethlans_worker_agent.idle_detection.session_win32')
session.start_session_monitor = lambda: None
sys.modules[session.__name__] = session

from sethlans_worker_agent import agent, api_handler, config, job_processor, system_monitor
from sethlans_worker_agent.cards import payload as card_payload


def heartbeat(payload):
    unreal = payload['unreal_capabilities']
    beats.append({'keys': sorted(payload), 'unreal_enabled': unreal.get('enabled'),
                  'unreal_reason': unreal.get('disabled_reason')})
    agent._shutdown_event.set()           # the main loop ends after the registration attempt
    return None


agent.configure_logging = lambda level: None
agent._run_setup_phase = lambda: True
agent._graceful_shutdown = lambda: None
system_monitor.get_system_info = lambda: {}
card_payload.add_to = lambda payload: None
api_handler.send_authenticated_heartbeat = heartbeat
config.API_TOKEN = 'made-up-token'

agent.main([])
system_monitor.WORKER_ID = 1
system_monitor.send_heartbeat()
poll = job_processor._unreal_poll_params()

reader = sys.modules.get('sethlans_worker_agent.gpu_memory.reader')
runtime = sys.modules.get('sethlans_worker_agent.unreal_executor.runtime')
print('RESULT ' + json.dumps({
    'modules': sorted(m for m in sys.modules if m.startswith('sethlans_worker_agent.gpu_memory.')),
    'reader_made': reader is not None and reader._reader is not None,
    'sampler': runtime is not None and runtime.sampler() is not None,
    'pdh_loads': [name for name in loads if 'pdh' in name.lower()],
    'smi_runs': [args for args in spawns if 'nvidia-smi' in args.lower()],
    'beats': beats,
    'poll': poll,
}))
"""


def _run_worker_start(tmp_path, extra_env):
    env = {key: value for key, value in os.environ.items() if not key.startswith('SETHLANS_')}
    env.update({'SETHLANS_WORKER_DATA_DIR': str(tmp_path), 'SETHLANS_UNREAL_ENABLED': 'false',
                'SETHLANS_DISABLE_RELEASE_FETCH': '1',
                'PYTHONPATH': os.pathsep.join([str(REPO / 'worker'), str(REPO)])})
    env.update(extra_env)
    completed = subprocess.run([sys.executable, '-c', _CHILD], capture_output=True, text=True, timeout=120,
                               env=env, cwd=str(tmp_path))
    assert completed.returncode == 0, completed.stderr[-4000:]
    [line] = [line for line in completed.stdout.splitlines() if line.startswith('RESULT ')]
    return json.loads(line[len('RESULT '):])


@pytest.mark.parametrize('extra_env', [{}, {'SETHLANS_UNREAL_VRAM_MB': '16384'}], ids=['plain', 'vram-override'])
def test_unreal_off_never_loads_the_windows_gpu_memory_backend(tmp_path, extra_env):
    result = _run_worker_start(tmp_path, extra_env)

    assert 'sethlans_worker_agent.gpu_memory.win_pdh' not in result['modules'], result['modules']
    assert 'sethlans_worker_agent.gpu_memory.win_backend' not in result['modules'], result['modules']
    assert 'sethlans_worker_agent.gpu_memory.win_crosscheck' not in result['modules'], result['modules']
    assert result['pdh_loads'] == [] and result['smi_runs'] == []
    assert result['reader_made'] is False and result['sampler'] is False


@pytest.mark.parametrize('extra_env', [{}, {'SETHLANS_UNREAL_VRAM_MB': '16384'}], ids=['plain', 'vram-override'])
def test_unreal_off_heartbeats_carry_no_gpu_memory(tmp_path, extra_env):
    result = _run_worker_start(tmp_path, extra_env)

    assert len(result['beats']) == 2  # registration, then the periodic heartbeat
    for beat in result['beats']:
        assert 'unreal_capabilities' in beat['keys'] and 'gpu_memory' not in beat['keys']
        assert beat['unreal_enabled'] is False
        assert not beat['unreal_reason'].startswith('Unreal support failed')  # off by config, not by a crash
    assert result['poll'] == {}
