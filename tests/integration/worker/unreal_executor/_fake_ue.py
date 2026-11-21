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

It fails like a crashed UE (a critical-error line, exit 3) when a warm-up or
render launch lacks exactly one ``-RenderOffscreen`` or one ``-dx12`` and
``-graphicsadapter=N`` pair, or the prep commandlet carries any of them, or
any launch lacks ``-UDPMESSAGING_TRANSPORT_ENABLE=0`` (#233), so every
passing test also proves the launches are headless, quiet and on the chosen
card. Warm-up and render log every D3D12 adapter (WARP and a virtual
adapter included, at their real indexes) and ``Chosen D3D12 Adapter Id = N``
with the ``N`` they got.

Behavior switches come from a ``fake_ue.json`` next to the ``.uproject``
(written by the test into the archive): ``cancel_first_render`` logs
``Compilation cancelled.`` on ``render.1``, ``prep_error`` makes the prep
fail, ``zen_path`` overrides the logged Zen data path, ``chosen_adapter``
overrides the logged adapter id (only in ``chosen_phase`` when given;
``game_overrides`` / ``texture_streaming`` set the prep's preset facts, default ``implicit`` / ``off``;
``after_chosen_sleep`` waits after it), ``adapters`` maps a
``-graphicsadapter`` index (as a string) to the ``[name, vendor, device,
revision]`` (hex strings) of the card logged there (default: one NVIDIA card),
``shots_result`` replaces fields of the ``list_shots`` result (shot discovery,
sequence rendering FR-SD-4: the prep alone answers with a shot list).

A sequence piece (``prepare_range``, FR-SW-3) renders every frame of the
params' ``range`` as ``.png`` (``size`` [w, h], default 1x1) or ``.exr`` (the
EXR magic and filler); ``skip_frame`` leaves one out, ``frame_sleep`` waits
between frames, ``draft_conflicts`` is the prep's list, and
``loads_in_flight`` ends the render on UE's audio streaming assertion
(critical error, exit 3) after every frame is written (FR-SW-8).

An audio job (``prepare_audio``, FR-SA) renders one stereo 48 kHz ``.wav`` of
the params' ``range`` at 30 fps (the prep reports ``frame_rate`` [30, 1]),
holding a seeded noise source ``audio_lag`` samples ahead (default 4,800);
the render fails like a crashed UE without the four audio flags, and
``audio_fault`` logs a ``seektable`` line. The export commandlet
(``export_audio``) writes that source as ``src_000.wav`` with one section,
or ``export_error`` as its error.
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


def png(path, w=1, h=1):
    raw = (b'\x00' + b'\xff\x00\x00' * w) * h
    chunk = lambda tag, data: struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def noise(n, seed=5):
    import random
    rnd, out, level = random.Random(seed), [], 0.3
    for i in range(n):
        if i % 4800 == 0:
            level = rnd.uniform(0.02, 0.5)
        out.append(level * (rnd.random() * 2 - 1))
    return out


def write_wav(path, samples):
    import array, wave
    with wave.open(path, 'wb') as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(48000)
        w.writeframes(array.array('h', [int(v * 30000) for v in samples for _ in (0, 1)]).tobytes())


def export_audio(params):
    end = params['range'][1]
    if OPTS.get('export_error'):
        return {'ok': False, 'error': OPTS['export_error'], 'sections': []}
    write_wav(os.path.join(params['export_dir'], 'src_000.wav'), noise((end + 60) * 1600))
    return {'ok': True, 'warnings': [], 'sections': [
        {'start': 0, 'end': end + 60, 'start_offset_samples': 0, 'volume': 1.0, 'looping': False,
         'wav': 'src_000.wav', 'sample_rate': 48000}, {'unsupported': 'a sound cue'}]}


def frame_file(path, ext):
    if ext == 'exr':
        with open(path, 'wb') as f:
            f.write(b'\x76\x2f\x31\x01' + b'\x00' * 60)
    else:
        png(path, *OPTS.get('size', [1, 1]))


# DXGI order as measured on a dev machine: the iGPU, WARP and a virtual display adapter that copies the real
# card's name and ids; the real card sits at the index the worker passed.
OTHER_ADAPTERS = [('AMD Radeon(TM) Graphics', '1002', '13c0', '00e5'),
                  ('Microsoft Basic Render Driver', '1414', '008c', '0000'),
                  ('NVIDIA GeForce RTX 5090', '10de', '2b85', '00a1')]


def d3d12_lines(phase):
    n = int(value('-graphicsadapter='))
    listed = list(OTHER_ADAPTERS)
    card = OPTS.get('adapters', {}).get(str(n), ('NVIDIA GeForce RTX 5090', '10de', '2b85', '00a1'))
    listed.insert(min(n, len(listed)), tuple(card))
    # The words are split so this .uproject passes the worker's project text guard (adapter_marker).
    found, chosen = 'LogD3D12RHI: Found D3D12' + ' adapter ', 'LogD3D12RHI: Chosen D3D12' + ' Adapter Id = '
    for i, (name, vendor, device, rev) in enumerate(listed):
        say(found + '%d: %s (VendorId: %s, DeviceId: %s, SubSysId: 0, Revision: %s' % (i, name, vendor, device, rev))
    if OPTS.get('chosen_phase', phase) != phase:
        say(chosen + str(n))
        return
    say(chosen + str(OPTS.get('chosen_adapter', n)))
    time.sleep(float(OPTS.get('after_chosen_sleep', 0)))


time.sleep(float(OPTS.get('phase_sleep', 0)))
if '-run=pythonscript' not in args and args.count('-RenderOffscreen') != 1:
    say('LogWindows: Error: === Critical error: === a window would open (no -RenderOffscreen)')
    sys.exit(3)
if args.count('-UDPMESSAGING_TRANSPORT_ENABLE=0') != 1:
    say('LogWindows: Error: === Critical error: === UdpMessaging would listen on 0.0.0.0:6666')
    sys.exit(3)
if '-run=pythonscript' in args and '-RenderOffscreen' in args:
    say('LogWindows: Error: === Critical error: === the prep commandlet got -RenderOffscreen')
    sys.exit(3)
adapter = [a for a in args if a.lower().startswith('-graphicsadapter')]
if '-run=pythonscript' in args and (adapter or '-dx12' in args):
    say('LogWindows: Error: === Critical error: === the prep commandlet got -dx12 or -graphicsadapter')
    sys.exit(3)
if '-run=pythonscript' not in args and (args.count('-dx12') != 1 or len(adapter) != 1):
    say('LogWindows: Error: === Critical error: === no single -dx12 -graphicsadapter=N pair')
    sys.exit(3)
say('LogZenServiceInstance: Found command line override ZenDataPath=' + zen)
say("LogZenServiceInstance: Display: Launching executable 'C:\\zen\\zenserver.exe', working dir 'C:\\zen', "
    "data dir '" + zen + "', args '--port " + port + " --data-dir \"" + zen + "\"'")
for name, val in cvars:
    say('LogDeviceProfileManager: Setting CommandLine Device Profile CVar: [[' + name + ':' + val + ']]')
job_dir = os.path.dirname(os.path.dirname(log_path))
attempt = os.path.basename(log_path).split('.')[1]

def shots_result(params):
    """Mode list_shots: a discovery with a name to clean and a duplicate; ``shots_result`` replaces fields."""
    facts = {'resolution': [1920, 1080], 'custom_frame_rate': None, 'frame_step': None, 'handle_frames': 0,
             'frame_number_offset': 0, 'zero_pad': 4, 'temporal_samples': 8, 'spatial_samples': 1,
             'layers_ok': True, 'can_png': True, 'can_exr': False, 'can_sound': True, 'game_overrides': 'preset',
             'texture_streaming': 'off'}
    result = {'ok': True, 'preset_kind': 'graph', 'playback_range': [0, 400], 'frame_rate': [30000, 1001],
              'shot_source': 'shot_tracks', 'shots': [['sh010', 0, 99], ['sh 020', 99, 202], ['sh010', 202, 400]],
              'preset_facts': facts, 'warnings': ['Shot sh030 has no camera cut inside the playback range.'],
              'scalability': 4, 'game_overrides': 'preset', 'texture_streaming': 'off'}
    result.update(OPTS.get('shots_result', {}))
    say('[SethlansPrep] listed ' + str(len(result['shots'])) + ' shots of ' + params['sequence'])
    return result


if '-run=pythonscript' in args:
    params = json.load(open(os.environ['SETHLANS_UE_JOB_PARAMS'], encoding='utf-8'))
    if params['mode'] == 'export_audio':
        json.dump(export_audio(params), open(params['result_path'], 'w', encoding='utf-8'))
        say('[SethlansAudioExport] wrote result')
        log.close()
        sys.exit(0)
    pilot = params['mode'] == 'prepare_pilot'
    frames = (params.get('pilot_frames') or OPTS.get('pilot_frames', [0, 99, 202])) if pilot else [params.get('frame')]
    result = {'ok': True, 'queue_asset': params.get('package_root', '') + '/Q_Job' + str(params['job_id']),
              'preset_kind': 'graph', 'playback_range': [0, 1928], 'camera_cut_starts': [0, 99, 202],
              'effective_cvars': {n: ('true' if v == '1' else v) for n, v in cvars}, 'queue_jobs': len(frames),
              'scalability': 4, 'warnings': [], 'game_overrides': OPTS.get('game_overrides', 'implicit'),
              'texture_streaming': OPTS.get('texture_streaming', 'off')}
    if pilot:
        result['pilot_frames'] = frames
    if params['mode'] in ('prepare_range', 'prepare_audio'):
        result.update(queue_jobs=1, draft_conflicts=OPTS.get('draft_conflicts', []), frame_rate=[30, 1])
    if params['mode'] == 'list_shots':
        result = shots_result(params)
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
    d3d12_lines('warmup')
    say('[Warm] done, quitting')
    json.dump({'ok': True}, open(params['result_path'], 'w', encoding='utf-8'))
    log.close()
    sys.exit(0)

prep = json.load(open(os.path.join(job_dir, 'prep_result.json'), encoding='utf-8'))
params = json.load(open(os.path.join(job_dir, 'params.json'), encoding='utf-8'))
frames = prep.get('pilot_frames') or [params.get('frame')]
ext, jobs = 'png', len(frames)
if params['mode'] == 'prepare_range':
    frames, ext, jobs = list(range(*params['range'])), params['output_format'].lower(), 1
d3d12_lines('render')
time.sleep(float(OPTS.get('render_sleep', 0.5)))
audio_flags = ['-deterministicaudio', '-audiomixer', '-AudioCallbackBufferFrameSize=32', '-AudioNumBuffersToEnqueue=2']
if (params['mode'] == 'prepare_audio') != all(flag in args for flag in audio_flags):
    say('LogWindows: Error: === Critical error: === the audio flags belong to an audio job only')
    sys.exit(3)
if params['mode'] == 'prepare_audio':
    first, end = params['range']
    lag, src = int(OPTS.get('audio_lag', 4800)), noise((end + 60) * 1600)
    write_wav(os.path.join(job_dir, 'output', 'sethlans_' + str(params['job_id']) + '.MasterSubmixDefault.wav'),
              [src[first * 1600 + p + lag] for p in range((end - first) * 1600)])
    frames = []
    if OPTS.get('audio_fault'):
        say('LogAudio: Error: seektable is corrupt')
for f in frames:
    if f != OPTS.get('skip_frame'):
        frame_file(os.path.join(job_dir, 'output', 'sethlans_' + str(params['job_id']) + '.' + '%04d' % f + '.' + ext), ext)
        time.sleep(float(OPTS.get('frame_sleep', 0)))
say('LogMovieRenderPipeline: Movie Graph Render completed. Duration: +00:00:05.250')
say('LogMovieRenderPipeline: MoviePipelineLinearExecutorBase finished ' + str(jobs) + ' jobs in +00:00:09.0.')
if OPTS.get('loads_in_flight'):
    say('LogWindows: Error: === Critical error: ===')
    say('LogWindows: Error: Assertion failed: NumberOfLoadsInFlight == 0 [File:AudioStreamingCache.cpp] [Line: 1]')
    log.close()
    sys.exit(3)
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
