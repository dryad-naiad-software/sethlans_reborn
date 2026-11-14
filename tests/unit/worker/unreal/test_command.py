# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""UE command lines and launch environments (FR-UE-6, FR-UE-7, FR-UE-26, FR-UE-27; AC-52, AC-53)."""
import pytest

from sethlans_worker_agent.unreal_executor import command, farm_defaults, user_state
from sethlans_worker_agent.unreal_executor.command import CommandError

EDITOR = r'C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
CACHE = r'D:\uecache'
UPROJECT = r'D:\uecache\w\0123456789abcdef1a2b3c4d-s0\ACOMAnimationSample\ACOMAnimationSample.uproject'
JOB = r'D:\uecache\j\42-1a2b3c4d'
MAP = '/Game/AnimationSample/Episodes/02_intro_beta/Intro_Beta_Master_lvl'
QUEUE = '/Game/_SethlansFarm/Job42/Q_Job42'
MERGED = [('gc.TimeBetweenPurgingPendingKillObjects', '4'), ('Constraints.NewEvaluation', '1'),
          ('r.RayTracing.ResidentGeometryMemoryPoolSizeInMB', '2048'), ('r.Streaming.PoolSize', '9000')]
DPCVARS = ('-DPCVars=gc.TimeBetweenPurgingPendingKillObjects=4,Constraints.NewEvaluation=1,'
           'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB=2048,r.Streaming.PoolSize=9000')
ZEN = [r'-ZenDataPath=D:\uecache\zen\data', r'-LocalDataCachePath=D:\uecache\zen\fs', '-SharedDataCachePath=None',
       '-ini:Engine:[Zen.AutoLaunch]:DesiredPort=8668,[Zen.AutoLaunch]:RemoteNetworkService=None']
TAIL = ZEN + ['-notraceserver', DPCVARS]


@pytest.fixture()
def ctx():
    return command.context_for(EDITOR, CACHE, 8668)


def test_render_golden(ctx):
    launch = command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\logs\render.1.log', MERGED)
    expected = [EDITOR, UPROJECT, MAP, '-game', f'-MoviePipelineConfig={QUEUE}', '-windowed', '-resx=1280',
                '-resy=720', '-unattended', '-nosplash', '-NoLoadingScreen', '-log', farm_defaults.INI_INPUT_OVERRIDE,
                r'-abslog=D:\uecache\j\42-1a2b3c4d\logs\render.1.log'] + TAIL
    assert list(launch.args) == expected
    assert launch.cwd == CACHE and launch.phase == 'render' and not launch.pilot
    assert 'SETHLANS_UE_JOB_PARAMS' not in launch.env


def test_pilot_render_carries_literal_csv_tokens_before_zen(ctx):
    launch = command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\logs\render.1.log', MERGED, pilot_launch=True)
    args = list(launch.args)
    assert args[-8:] == ['-csvCaptureFrames=5000000', '-csvGpuStats'] + TAIL


def test_prep_golden(ctx):
    launch = command.build_prep(ctx, UPROJECT, JOB + r'\scripts\sethlans_prepare_queue.py',
                                JOB + r'\logs\prep.1.log', JOB + r'\params.json', MERGED)
    assert list(launch.args) == [EDITOR, UPROJECT, '-run=pythonscript',
                                 r'-script=D:\uecache\j\42-1a2b3c4d\scripts\sethlans_prepare_queue.py',
                                 '-unattended', '-nosplash', '-log',
                                 r'-abslog=D:\uecache\j\42-1a2b3c4d\logs\prep.1.log'] + TAIL
    assert launch.env['SETHLANS_UE_JOB_PARAMS'] == JOB + r'\params.json'


def test_warmup_golden(ctx):
    launch = command.build_warmup(ctx, UPROJECT, JOB + r'\scripts\sethlans_warm_up.py',
                                  JOB + r'\logs\warmup.1.log', JOB + r'\params.json', MERGED)
    assert list(launch.args) == [EDITOR, UPROJECT, '-unattended', '-nosplash', '-log',
                                 r'-abslog=D:\uecache\j\42-1a2b3c4d\logs\warmup.1.log',
                                 r'-ExecCmds=py D:\uecache\j\42-1a2b3c4d\scripts\sethlans_warm_up.py'] + TAIL


def test_every_launch_has_the_isolation_environment(ctx, monkeypatch):
    monkeypatch.setenv('USERPROFILE', r'C:\Users\artist')
    monkeypatch.setenv('SETHLANS_API_TOKEN', 'secret')
    monkeypatch.setenv('UE-ZenDataPath', r'C:\elsewhere')
    for launch in (command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED),
                   command.build_prep(ctx, UPROJECT, JOB + r'\s.py', JOB + r'\l.log', JOB + r'\p.json', MERGED)):
        env = launch.env
        assert env['USERPROFILE'] == r'D:\uecache\ueprofile\Profile'
        assert env['LOCALAPPDATA'] == r'D:\uecache\ueprofile\Profile\AppData\Local'
        assert env['APPDATA'] == r'D:\uecache\ueprofile\Profile\AppData\Roaming'
        assert env['HOMEDRIVE'] == 'D:' and env['HOMEPATH'] == r'\uecache\ueprofile\Profile'
        assert env['TEMP'] == env['TMP'] == r'D:\uecache\ueprofile\Temp'
        assert 'SETHLANS_API_TOKEN' not in env
        assert not any(key.upper() == 'UE-ZENDATAPATH' for key in env)
        assert launch.args.count('-notraceserver') == 1


def _render_args(ctx):
    return list(command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\logs\render.1.log', MERGED).args)


@pytest.mark.parametrize('mutate', [
    lambda a: a.insert(3, 'two words'),
    lambda a: a.insert(3, '-x="quoted"'),
    lambda a: a.insert(3, '-x=\x01'),
    lambda a: a.__setitem__(0, r'C:\Other\UnrealEditor-Cmd.exe'),
    lambda a: a.__setitem__(1, r'w\relative\Proj.uproject'),
    lambda a: a.__setitem__(13, r'-abslog=logs\render.1.log'),
    lambda a: a.__setitem__(13, r'-abslog=D:\uecache\j\..\render.1.log'),
    lambda a: a.append('-log'),                                   # a switch after -DPCVars
    lambda a: a.remove(ZEN[0]),                                   # missing Zen token
    lambda a: a.insert(14, ZEN[1]),                               # duplicated Zen token
    lambda a: a.insert(14, '-ini:Engine:[Core.Log]:Global=All'),  # another -ini:Engine: token
    lambda a: a.__setitem__(a.index(ZEN[3]), '-ini:Engine:[Zen.AutoLaunch]:DesiredPort=8668'),
    lambda a: a.remove('-SharedDataCachePath=None'),
    lambda a: a.__setitem__(a.index(ZEN[2]), r'-SharedDataCachePath=\\nas\ddc'),
    lambda a: a.__setitem__(13, '-abslog=D:/uecache/j/render.1.log'),        # forward slashes (Sec S7)
    lambda a: a.__setitem__(13, r'-abslog=D:\uecache\j/../render.1.log'),     # mixed separators
    lambda a: a.__setitem__(a.index(ZEN[0]), r'-ZenDataPath=D:\uecache2\zen\data'),
    lambda a: a.remove('-notraceserver'),
    lambda a: a.insert(14, '-notraceserver'),                     # before the Zen tokens
    lambda a: a.insert(14, '-csvGpuStats'),                       # CSV on a non-pilot launch
])
def test_validate_args_rejects(ctx, mutate):
    args = _render_args(ctx)
    command.validate_args(args, ctx)
    mutate(args)
    with pytest.raises(CommandError):
        command.validate_args(args, ctx)


def test_exec_cmds_path_must_be_under_cache_and_allowlisted(ctx):
    for script in (r'D:\other\warm.py', r'D:\uecache\j\42\a,b.py', r'D:\uecache\j\42\a b.py'):
        with pytest.raises(CommandError):
            command.build_warmup(ctx, UPROJECT, script, JOB + r'\l.log', JOB + r'\p.json', MERGED)


def test_prep_script_must_be_under_cache(ctx):
    with pytest.raises(CommandError):
        command.build_prep(ctx, UPROJECT, r'D:\other\prep.py', JOB + r'\l.log', JOB + r'\p.json', MERGED)


def test_relative_params_path_is_rejected(ctx):
    with pytest.raises(CommandError):
        command.build_prep(ctx, UPROJECT, JOB + r'\s.py', JOB + r'\l.log', r'params.json', MERGED)


def test_pilot_validation_requires_both_csv_tokens(ctx):
    args = list(command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED, pilot_launch=True).args)
    command.validate_args(args, ctx, pilot_launch=True)
    args.remove('-csvGpuStats')
    with pytest.raises(CommandError):
        command.validate_args(args, ctx, pilot_launch=True)


@pytest.mark.parametrize('merged', [[], [('r.x_abslog', '1')], [('a.cvarsini', '1')], [('r.a', '1'), ('R.A', '2')]])
def test_dpcvars_rejects(merged):
    with pytest.raises(CommandError):
        command.dpcvars_token(merged)


def test_map_and_queue_are_revalidated(ctx):
    with pytest.raises(CommandError):
        command.build_render(ctx, UPROJECT, '-abslog=x', QUEUE, JOB + r'\l.log', MERGED)
    with pytest.raises(CommandError):
        command.build_render(ctx, UPROJECT, MAP, 'Game/x', JOB + r'\l.log', MERGED)


def test_zen_port_8558_is_refused():
    ctx = command.context_for(EDITOR, CACHE, 8558)
    with pytest.raises(ValueError):
        command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED)


def test_args0_with_spaces_is_the_only_whitespace_token(ctx):
    assert ' ' in EDITOR
    assert sum(1 for token in _render_args(ctx) if ' ' in token) == 1


def test_isolation_args_and_profile(ctx):
    assert user_state.isolation_args() == ('-notraceserver',)
    assert ctx.profile == r'D:\uecache\ueprofile'
