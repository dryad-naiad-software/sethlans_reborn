# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``-dx12`` and ``-graphicsadapter=N`` (card selection FR-CU-2, B5, C-15; AC-12).

Warm-up and render (and a pilot render) carry each exactly once, before the
Zen tokens, with ``N`` the chosen card's DXGI index (0-63); the prep
commandlet carries neither.
"""
import pytest

from sethlans_worker_agent.unreal_executor import command
from sethlans_worker_agent.unreal_executor.command import CommandError
from tests.unit.worker.unreal.test_command import CACHE, EDITOR, JOB, MAP, MERGED, QUEUE, UPROJECT, ZEN

DX12, ADAPTER = '-dx12', '-graphicsadapter=1'


def _ctx(index=1):
    return command.context_for(EDITOR, CACHE, 8668, graphics_adapter=index)


def _launches(ctx):
    return {
        'warmup': command.build_warmup(ctx, UPROJECT, JOB + r'\s.py', JOB + r'\l.log', JOB + r'\p.json', MERGED),
        'render': command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED),
        'pilot': command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED, pilot_launch=True),
    }


@pytest.mark.parametrize('name', ['warmup', 'render', 'pilot'])
def test_once_each_before_the_zen_tokens(name):
    args = list(_launches(_ctx())[name].args)
    assert args.count(DX12) == 1 and args.count(ADAPTER) == 1
    assert args.index(DX12) + 1 == args.index(ADAPTER) < args.index(ZEN[0])


def test_the_prep_has_neither():
    prep = command.build_prep(_ctx(), UPROJECT, JOB + r'\s.py', JOB + r'\l.log', JOB + r'\p.json', MERGED)
    assert not [token for token in prep.args if token.lower() in (DX12,) or token.lower().startswith('-graphicsad')]


@pytest.mark.parametrize('index', [0, 7, 63])
def test_n_is_the_chosen_index(index):
    assert f'-graphicsadapter={index}' in _launches(_ctx(index))['render'].args


@pytest.mark.parametrize('index', [None, -1, 64, True, '1', 1.0])
def test_a_launch_without_a_valid_index_is_refused(index):
    with pytest.raises(CommandError, match='DXGI index'):
        command.build_render(_ctx(index), UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED)
    with pytest.raises(CommandError, match='DXGI index'):
        command.build_warmup(_ctx(index), UPROJECT, JOB + r'\s.py', JOB + r'\l.log', JOB + r'\p.json', MERGED)


@pytest.mark.parametrize('mutate', [
    lambda a: a.remove(DX12),
    lambda a: a.remove(ADAPTER),
    lambda a: a.insert(3, DX12),                                              # twice
    lambda a: a.insert(3, '-graphicsadapter=2'),                              # twice, another value
    lambda a: a.__setitem__(a.index(DX12), '-DX12'),                          # other spelling
    lambda a: a.__setitem__(a.index(ADAPTER), '-graphicsadapter=64'),         # out of range
    lambda a: a.__setitem__(a.index(ADAPTER), '-graphicsadapter=01'),         # not the worker's form
    lambda a: a.__setitem__(a.index(ADAPTER), '-graphicsadapter=x'),
    lambda a: a.__setitem__(a.index(ADAPTER), '-graphicsadapter'),
    lambda a: a.insert(a.index('-notraceserver'), a.pop(a.index(ADAPTER))),  # after the Zen tokens
    lambda a: a.insert(a.index('-notraceserver'), a.pop(a.index(DX12))),
])
@pytest.mark.parametrize('phase', ['warmup', 'render'])
def test_validate_args_enforces_the_adapter_rule(mutate, phase):
    ctx = _ctx()
    args = list(_launches(ctx)[phase].args)
    command.validate_args(args, ctx, phase=phase)
    mutate(args)
    with pytest.raises(CommandError):
        command.validate_args(args, ctx, phase=phase)


@pytest.mark.parametrize('token', ['-gpupreference=1', '-GPUPreference=2', '-r.x=graphicsAdapter=0',
                                   r'-abslog=D:\uecache\graphicsadapter=1\l.log', '-graphicsadapter=1x'])
@pytest.mark.parametrize('phase', ['prep', 'warmup', 'render', None])
def test_no_other_token_may_steer_the_adapter(token, phase):
    """Security review: UE finds ``graphicsadapter=``/``gpupreference=`` anywhere (case-insensitive)."""
    ctx = _ctx()
    if phase == 'prep':
        args = list(command.build_prep(ctx, UPROJECT, JOB + r'\s.py', JOB + r'\l.log', JOB + r'\p.json', MERGED).args)
    else:
        args = list(_launches(ctx)[phase or 'render'].args)
    command.validate_args(args, ctx, phase=phase)
    args.insert(3, token)
    with pytest.raises(CommandError):
        command.validate_args(args, ctx, phase=phase)


def test_the_dx12_fallback_is_a_warning_in_the_summary():
    """C-15 visibility: a project whose data cannot run on D3D12 falls back to another RHI."""
    from sethlans_worker_agent.unreal_executor import log_summary, log_validation
    scan = log_validation.LogScan()
    log_validation.scan_line("[x][  0]LogRHI: Warning: User requested RHI 'D3D12' but that is not supported by this "
                             "project's data, ignoring.", scan, log_validation.line_rules())
    values, warnings = log_summary.render_values(scan, MERGED)
    assert values['dx12_unsupported'] == 1 and any(w.startswith('dx12_unsupported') for w in warnings)


@pytest.mark.parametrize('token', [DX12, ADAPTER])
def test_validate_args_refuses_either_token_in_the_prep(token):
    ctx = _ctx()
    args = list(command.build_prep(ctx, UPROJECT, JOB + r'\s.py', JOB + r'\l.log', JOB + r'\p.json', MERGED).args)
    command.validate_args(args, ctx, phase='prep')
    args.insert(3, token)
    with pytest.raises(CommandError):
        command.validate_args(args, ctx, phase='prep')
