# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared plumbing for the card-choice integration tests (card selection, chunk 2).

Unlike ``_capacity_harness.install_fake_capacity``, which hands
``init_capacity`` a ready-made card selection, ``install_card_choice`` runs
the real path: the saved ``compute`` section is parsed by ``config_compute``,
the fake OS card list is joined with the cached Blender list by
``cards.inventory``, ``cards.in_use`` picks the ticked cards and
``compute_capacity_profile`` builds the slots. Only the OS probe, the
Blender detection cache, the saved file and the CPU count are faked.

``FakeBlender`` replaces ``subprocess.Popen`` inside ``blender_executor``.
It runs the generated render script against the fake ``bpy`` of
``tests/unit/worker/_fake_bpy.py`` and records which device entries the
script switched on, then writes the output file and exits 0.

Module name starts with an underscore so pytest does not collect it.
"""

import io
import os
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace

from sethlans_worker_agent import api_handler, asset_manager, blender_executor, config
from sethlans_worker_agent import config_compute, job_processor
from sethlans_worker_agent.cards import inventory, os_probe, startup_log
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.tool_manager import tool_manager_instance

from tests.unit.worker._fake_bpy import FakePrefs, make_scene

# Three cards of one family; card B sits between the two that tests tick.
_CARDS = (
    ('pci:0000:01:00.0', 'Card A', 0x2684),
    ('pci:0000:02:00.0', 'Card B', 0x2704),
    ('pci:0000:03:00.0', 'Card C', 0x2782),
)
KEY_A, KEY_B, KEY_C = (key for key, _name, _device in _CARDS)
ID_A, ID_B, ID_C = (f'CUDA_{name}_{key[4:-2]}' for key, name, _device in _CARDS)
VENDOR = 0x10de
BLENDER_LIST = [{'index': i, 'name': name, 'type': 'CUDA', 'id': f'CUDA_{name}_{key[4:-2]}'}
                for i, (key, name, _device) in enumerate(_CARDS)]
# Blender's prefs.devices on this fake machine: one CUDA entry per card, plus the CPU.
DEVICE_SPECS = [('CUDA', record['id'], record['name']) for record in BLENDER_LIST] + [('CPU', 'CPU', 'CPU')]


def saved_record(key):
    """The record ``cards.store`` saves for one of the fake cards."""
    for card_key, name, device in _CARDS:
        if card_key == key:
            return {'key': key, 'vendor_id': VENDOR, 'device_id': device, 'revision_id': None, 'name': name}
    raise KeyError(key)


def install_card_choice(mocker, *, method, gpu_mode='split', ticked=(), present=None, cpu_cores=8):
    """Fake the probes and the saved choice, then call the real ``init_capacity``.

    ``present`` are the keys the OS probe lists (default: all three cards).
    Returns ``(capacity, probe_mock)``.
    """
    present = tuple(key for key, _n, _d in _CARDS) if present is None else tuple(present)
    os_cards = tuple(OsCard(key=key, name=name, vendor_id=VENDOR, device_id=device)
                     for key, name, device in _CARDS if key in present)
    probe = mocker.patch.object(os_probe, 'probe', return_value=os_cards)
    mocker.patch.object(startup_log, 'cached_blender_list', return_value=([dict(r) for r in BLENDER_LIST], '4.5.1'))
    real_build = inventory.build
    # Join as on Windows and Linux on every CI runner (macOS has its own one-card rule, D9).
    mocker.patch.object(inventory, 'build', side_effect=lambda os_list, blender_list, version=None, **_kw:
                        real_build(os_list, blender_list, version, platform_name='linux'))
    raw = {'method': method, 'gpu_mode': gpu_mode, 'cards': [saved_record(key) for key in ticked],
           'known': [key for key, _n, _d in _CARDS], 'revision': 1}
    mocker.patch.object(config_compute, '_read_raw', return_value=raw)
    mocker.patch.object(job_processor.system_monitor, 'get_gpu_device_details',
                        return_value=[dict(r) for r in BLENDER_LIST])
    mocker.patch.object(job_processor.system_monitor, 'get_cpu_thread_count', return_value=cpu_cores)
    mocker.patch.object(config, 'CPU_THREADS', 0)
    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders', return_value=[])
    return job_processor.init_capacity(), probe


def blender_job(job_id, render_device):
    """A single-frame Cycles job dict as the manager's poll returns it."""
    return {
        'id': job_id, 'name': f'Job-{job_id}', 'status': 'QUEUED',
        'render_device': render_device, 'render_engine': 'CYCLES',
        'renderer': 'BLENDER', 'blender_version': '4.5.1',
        'asset': {'id': 1, 'blend_file': f'scene-{job_id}.blend'},
        'output_file_pattern': f'job_{job_id}/frame_####.png',
        'start_frame': 1, 'end_frame': 1, 'render_settings': {},
    }


class _FakeProcess:
    def __init__(self, returncode, stderr=''):
        self.pid = 4242
        self.returncode = returncode
        self.stdout = io.StringIO('')
        self.stderr = io.StringIO(stderr)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class FakeBlender:
    """Stands in for ``subprocess.Popen`` in ``blender_executor``.

    ``runs`` maps job id to ``{'command', 'enabled', 'device', 'compute_type'}``
    where ``enabled`` lists the ``(type, id)`` entries left switched on.
    With ``overlap`` set, each launch waits until that many renders have
    started, so their slot reservations are held at the same time.
    """

    def __init__(self, monkeypatch, overlap=0):
        self.runs = {}
        self.launches = 0
        self._exec_lock = threading.Lock()
        self._barrier = threading.Barrier(overlap, timeout=10) if overlap else None
        monkeypatch.setitem(sys.modules, 'bpy', None)  # restored after the test

    def popen(self, command, **_kwargs):
        script_path = command[command.index('--python') + 1]
        with open(script_path, encoding='utf-8') as handle:
            script = handle.read()
        job_id = int(os.path.basename(os.path.dirname(command[command.index('-o') + 1]))[len('job_'):])
        prefs, scene, code = self.run_script(script)
        with self._exec_lock:
            self.launches += 1
            self.runs[job_id] = {'command': list(command), 'enabled': prefs.enabled(),
                                 'device': scene.cycles.device, 'compute_type': prefs.compute_device_type}
        if self._barrier is not None:
            self._barrier.wait()
        output = command[command.index('-o') + 1].replace('####', '0001')
        with open(output, 'wb') as handle:
            handle.write(b'fake render')
        return _FakeProcess(code)

    def run_script(self, script):
        """Execute a generated script against a fresh fake Blender; return ``(prefs, scene, exit_code)``."""
        prefs = FakePrefs(DEVICE_SPECS)
        scene = make_scene()
        bpy = ModuleType('bpy')
        bpy.context = SimpleNamespace(scene=scene, preferences=SimpleNamespace(
            addons={'cycles': SimpleNamespace(preferences=prefs)}))
        bpy.data = SimpleNamespace(scenes=[scene])
        with self._exec_lock:  # one script at a time owns sys.modules['bpy']
            sys.modules['bpy'] = bpy
            try:
                exec(compile(script, '<render-script>', 'exec'), {'__name__': '__render_script__'})
                code = 0
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return prefs, scene, code


def install_render_path(mocker, monkeypatch, tmp_path, overlap=0):
    """Fake the manager API, the asset and the Blender download; return ``(blender, api)``."""
    blend = tmp_path / 'scene.blend'
    blend.write_bytes(b'BLENDER')
    mocker.patch.object(config, 'WORKER_TEMP_DIR', str(tmp_path / 'temp'))
    mocker.patch.object(config, 'WORKER_OUTPUT_DIR', str(tmp_path / 'output'))
    mocker.patch.object(config, 'IDLE_DETECTION_ENABLED', False)
    mocker.patch.object(asset_manager, 'ensure_asset_is_available', return_value=str(blend))
    mocker.patch.object(tool_manager_instance, 'ensure_blender_version_available', return_value='fake-blender')
    mocker.patch.object(tool_manager_instance, 'acquire_version')
    mocker.patch.object(tool_manager_instance, 'release_version')
    blender = FakeBlender(monkeypatch, overlap=overlap)
    popen = mocker.Mock(side_effect=blender.popen)
    # Only blender_executor's view of subprocess changes; other code keeps the real Popen.
    fake_subprocess = SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired)
    mocker.patch.object(blender_executor, 'subprocess', fake_subprocess)
    api = {
        'poll': mocker.patch.object(api_handler, 'poll_for_available_jobs', return_value=[]),
        'claim': mocker.patch.object(api_handler, 'claim_job', return_value=True),
        'update': mocker.patch.object(api_handler, 'update_job_status', return_value=None),
        'upload': mocker.patch.object(api_handler, 'upload_render_output', return_value=True),
        'popen': popen,
    }
    return blender, api


def final_status(api, job_id):
    """The last status payload reported for ``job_id``."""
    payloads = [c.args[1] for c in api['update'].call_args_list if c.args[0] == job_id]
    assert payloads, f'no status was reported for job {job_id}'
    return payloads[-1]
