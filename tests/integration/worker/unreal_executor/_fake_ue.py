# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A fake ``UnrealEditor-Cmd.exe`` for the executor integration tests (Windows).

``make_fake_engine(root)`` copies the base interpreter as
``<root>/Engine/Binaries/Win64/UnrealEditor-Cmd.exe`` with a ``pyvenv.cfg``
pointing at its stdlib. UE's command line starts with the ``.uproject``
path, so the interpreter runs **the .uproject file as a Python script**: the
test archive's ``.uproject`` is ``FAKE_UE_SCRIPT``. It writes realistic log
lines (the Zen override and launch lines, the D3D12 adapter, the MRQ
completion lines, ``RequestExitWithStatus(0, 0``), the prep result, the
warm-up marker line, and PNGs into ``j/<job>/output``.

Behavior switches come from a ``fake_ue.json`` next to the ``.uproject``
(written by the test into the archive): ``cancel_first_render`` logs
``Compilation cancelled.`` on ``render.1``, ``prep_error`` makes the prep
fail, ``zen_path`` overrides the logged Zen data path.
"""
import glob
import shutil
import sys
from pathlib import Path

FAKE_UE_SCRIPT = r'''
import json, os, struct, sys, time, zlib

args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(here, 'fake_ue.json'), encoding='utf-8') as f:
        OPTS = json.load(f)
except OSError:
    OPTS = {}


def value(prefix):
    for a in args:
        if a.lower().startswith(prefix.lower()):
            return a[len(prefix):]
    return None


log_path = value('-abslog=')
zen = OPTS.get('zen_path') or value('-ZenDataPath=')
port = (value('-ini:Engine:[Zen.AutoLaunch]:DesiredPort=') or '0').split(',')[0]
cvars = [kv.split('=', 1) for kv in (value('-DPCVars=') or '').split(',') if '=' in kv]
log = open(log_path, 'a', encoding='utf-8', buffering=1)


def say(text):
    log.write('[2026.11.14-00.00.00:000][  0]' + text + '\n')


def png(path):
    raw = b'\x00\xff\x00\x00'
    chunk = lambda tag, data: struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


time.sleep(float(OPTS.get('phase_sleep', 0)))
say('LogZenServiceInstance: Found command line override ZenDataPath=' + zen)
say("LogZenServiceInstance: Display: Launching executable 'C:\\zen\\zenserver.exe', working dir 'C:\\zen', "
    "data dir '" + zen + "', args '--port " + port + " --data-dir \"" + zen + "\"'")
for name, val in cvars:
    say('LogDeviceProfileManager: Setting CommandLine Device Profile CVar: [[' + name + ':' + val + ']]')
job_dir = os.path.dirname(os.path.dirname(log_path))
attempt = os.path.basename(log_path).split('.')[1]

if '-run=pythonscript' in args:
    params = json.load(open(os.environ['SETHLANS_UE_JOB_PARAMS'], encoding='utf-8'))
    pilot = params['mode'] == 'prepare_pilot'
    frames = OPTS.get('pilot_frames', [0, 99, 202]) if pilot else [params['frame']]
    result = {'ok': True, 'queue_asset': params['package_root'] + '/Q_Job' + str(params['job_id']),
              'preset_kind': 'graph', 'playback_range': [0, 1928], 'camera_cut_starts': [0, 99, 202],
              'effective_cvars': {n: ('true' if v == '1' else v) for n, v in cvars}, 'queue_jobs': len(frames),
              'scalability': 4, 'warnings': []}
    if pilot:
        result['pilot_frames'] = frames
    if OPTS.get('prep_error'):
        result = {'ok': False, 'error': OPTS['prep_error']}
    json.dump(result, open(params['result_path'], 'w', encoding='utf-8'))
    say('[SethlansPrep] wrote result')
    log.close()
    sys.exit(0 if result['ok'] else -1)

if any(a.startswith('-ExecCmds=py ') for a in args):
    params = json.load(open(os.environ['SETHLANS_UE_JOB_PARAMS'], encoding='utf-8'))
    say('[Warm] scalability ' + str(params['scalability']))
    say('[Warm] 2 deformers loaded')
    say('[Warm] done, quitting')
    json.dump({'ok': True}, open(params['result_path'], 'w', encoding='utf-8'))
    log.close()
    sys.exit(0)

prep = json.load(open(os.path.join(job_dir, 'prep_result.json'), encoding='utf-8'))
params = json.load(open(os.path.join(job_dir, 'params.json'), encoding='utf-8'))
frames = prep.get('pilot_frames') or [params['frame']]
say('LogD3D12RHI: Found D3D12 adapter 0: NVIDIA GeForce RTX 5090 (VendorId: 10de, DeviceId: 2b85, SubSysId: 0, '
    'Revision: 00a1')
say('LogD3D12RHI: Chosen D3D12 Adapter Id = 0')
time.sleep(float(OPTS.get('render_sleep', 0.5)))
for f in frames:
    png(os.path.join(job_dir, 'output', 'sethlans_' + str(params['job_id']) + '.' + '%04d' % f + '.png'))
say('LogMovieRenderPipeline: Movie Graph Render completed. Duration: +00:00:05.250')
say('LogMovieRenderPipeline: MoviePipelineLinearExecutorBase finished ' + str(len(frames)) + ' jobs in +00:00:09.0.')
say('LogCore: FPlatformMisc::RequestExitWithStatus(0, 0, <NoCallSiteInfo>)')
if OPTS.get('cancel_first_render') and attempt == '1':
    say('LogComputeFramework: Warning: DG_LatticeDeformer: Compilation cancelled.')
log.close()
sys.exit(0)
'''


def make_fake_engine(root: Path) -> Path:
    """``<root>/Engine/Binaries/Win64/UnrealEditor-Cmd.exe`` (the base interpreter + pyvenv.cfg)."""
    binaries = Path(root) / 'Engine' / 'Binaries' / 'Win64'
    binaries.mkdir(parents=True, exist_ok=True)
    base = Path(sys.base_prefix)
    shutil.copy2(base / 'python.exe', binaries / 'UnrealEditor-Cmd.exe')
    for dll in glob.glob(str(base / '*.dll')):
        shutil.copy2(dll, binaries / Path(dll).name)
    (binaries / 'pyvenv.cfg').write_text(f'home = {base}\ninclude-system-site-packages = false\n', encoding='utf-8')
    return binaries / 'UnrealEditor-Cmd.exe'
