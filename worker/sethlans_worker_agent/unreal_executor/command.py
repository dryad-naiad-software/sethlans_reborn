# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
UE command lines and launch environments (Unreal foundation FR-UE-6,
FR-UE-7, FR-UE-8, FR-UE-9, FR-UE-26, FR-UE-27; chunk 7 integration
requirements 10 and 11).

Every UE launch (prep, warm-up, render, pilot render) is built by **one**
function, ``_finish``, which appends, in this order and exactly once::

    [-csvCaptureFrames=5000000 -csvGpuStats]   pilot render only
    *zen_private.zen_flags(CACHE_DIR, ZEN_PORT)
    *user_state.isolation_args()               (-notraceserver)
    -DPCVars=<merged>                          always last

and builds the environment as ``env_cleanup.clean_env_for_unreal(params)``
plus ``user_state.isolation_env(P)``. No launch path can omit either.
``validate_args`` re-checks the result and raises ``CommandError``:

* no token contains ``"``, NUL or a control character;
* ``args[0]`` equals the detected install's ``editor_cmd`` exactly and is
  the only token allowed whitespace, apart from ``-ExecCmds=py <path>``;
* every path (``.uproject``, ``-abslog=``, ``-script=``, the ``-ExecCmds``
  path, ``-ZenDataPath=``, ``-LocalDataCachePath=``, ``SETHLANS_UE_JOB_PARAMS``)
  matches ``^[A-Za-z]:\\`` with no ``.`` or ``..`` component; the script
  paths lie under ``CACHE_DIR`` and match the FR-WC-3 allowlist;
* the Zen tokens, ``-notraceserver`` and (pilot) the CSV tokens appear
  exactly once each, in the order above; no other ``-ini:Engine:``,
  ``-ZenDataPath=``, ``-LocalDataCachePath=`` or ``-csv`` token appears;
* ``-DPCVars=`` is the last token.

Commands are lists for ``Popen(shell=False)``; nothing job-derived is
interpolated into Python source (FR-UE-7).
"""
import ntpath
import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

from sethlans_worker_agent import env_cleanup
from sethlans_worker_agent.unreal_executor import farm_defaults, pilot, user_state, zen_private
from sethlans_worker_agent.unreal_executor.cache_dir import ALLOWLIST
from shared.unreal import validation

PHASES = ('prep', 'warmup', 'render')
DPCVARS = '-DPCVars='
EXEC_PY = '-ExecCmds=py '
_ABS = re.compile(r'^[A-Za-z]:\\', re.ASCII)
_CONTROL = re.compile(r'[\x00-\x1f\x7f"]')
_WHITESPACE = re.compile(r'\s')
_PATH_PREFIXES = ('-abslog=', '-script=', '-zendatapath=', '-localdatacachepath=')


class CommandError(ValueError):
    """A UE command line or launch environment breaks an FR-UE-7 rule."""


@dataclass(frozen=True)
class LaunchContext:
    editor_cmd: str     # str(EngineInstall.editor_cmd), never job data
    cache_dir: str      # CACHE_DIR (validated by FR-WC-3)
    zen_port: int       # config_unreal.ZEN_PORT
    profile: str        # user_state.profile_dir(CACHE_DIR)


@dataclass(frozen=True)
class UeLaunch:
    phase: str
    pilot: bool
    args: Tuple[str, ...]
    env: Mapping[str, str]
    cwd: str
    profile: str


def context_for(editor_cmd, cache_dir, zen_port: int) -> LaunchContext:
    return LaunchContext(str(editor_cmd), str(cache_dir), int(zen_port),
                         str(user_state.profile_dir(cache_dir)))


def dpcvars_token(merged: Sequence[Tuple[str, str]]) -> str:
    """``-DPCVars=k=v,...``; every name and value re-validated (FR-UE-5)."""
    pairs = list(merged)
    if not pairs:
        raise CommandError("a UE launch needs at least the farm CVars")
    try:
        checked = validation.validate_cvars(dict(pairs))
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    if len(checked) != len(pairs):
        raise CommandError("a CVar is listed twice")
    return DPCVARS + ','.join(f"{name}={checked[name]}" for name, _value in pairs)


def _is_abs(path: str) -> bool:
    """``^[A-Za-z]:\\``, backslashes only (a ``/`` is refused), no ``.`` or ``..`` component (Sec S7)."""
    if '/' in path or not _ABS.match(path):
        return False
    return not any(part in ('.', '..') for part in re.split(r'[\\/]', path))


def _under_cache(path: str, cache_dir: str) -> bool:
    root = ntpath.normcase(ntpath.normpath(cache_dir)).rstrip('\\') + '\\'
    return ntpath.normcase(path).startswith(root) and bool(ALLOWLIST.match(path))


def _require_abs(value: str, what: str) -> None:
    if not _is_abs(value):
        raise CommandError(f"{what} must be an absolute local path without '.' or '..' ({value!r})")


def _check_exec_py(token: str, ctx: LaunchContext) -> None:
    script = token[len(EXEC_PY):]
    _require_abs(script, "the -ExecCmds script")
    if not _under_cache(script, ctx.cache_dir):
        raise CommandError("the -ExecCmds script must be under CACHE_DIR (FR-WC-3 allowlist)")


def _check_paths(token: str, ctx: LaunchContext) -> None:
    lowered = token.lower()
    for prefix in _PATH_PREFIXES:
        if lowered.startswith(prefix):
            _require_abs(token[len(prefix):], prefix.rstrip('='))
    if lowered.startswith('-script=') and not _under_cache(token[len('-script='):], ctx.cache_dir):
        raise CommandError("the -script path must be under CACHE_DIR (FR-WC-3 allowlist)")


def _check_token(index: int, token: str, ctx: LaunchContext) -> None:
    if not isinstance(token, str) or not token:
        raise CommandError(f"token {index} is empty or not a string")
    if _CONTROL.search(token):
        raise CommandError(f"token {index} contains a quote or a control character")
    if index == 0:
        if token != ctx.editor_cmd:
            raise CommandError("args[0] is not the detected UnrealEditor-Cmd.exe")
    elif token.lower().startswith(EXEC_PY.lower()):
        _check_exec_py(token, ctx)
    elif _WHITESPACE.search(token):
        raise CommandError(f"token {index} contains whitespace")
    else:
        _check_paths(token, ctx)


def _positions(args: Sequence[str], predicate) -> list:
    return [index for index, token in enumerate(args) if predicate(token)]


def _exactly_once(args: Sequence[str], value: str, prefix: Optional[str] = None) -> int:
    folded = (prefix or value).lower()
    found = _positions(args, lambda t: t.lower().startswith(folded) if prefix else t.lower() == folded)
    if len(found) != 1 or args[found[0]] != value:
        raise CommandError(f"{value!r} must appear exactly once (found {len(found)})")
    return found[0]


def _check_order(args: Sequence[str], ctx: LaunchContext, pilot_launch: bool) -> None:
    data, fs, shared, ini = zen_private.zen_flags(ctx.cache_dir, ctx.zen_port)
    zen = [_exactly_once(args, data, '-zendatapath='), _exactly_once(args, fs, '-localdatacachepath='),
           _exactly_once(args, shared, '-shareddatacachepath='), _exactly_once(args, ini, '-ini:engine:')]
    trace = _exactly_once(args, user_state.NOTRACESERVER)
    last = len(args) - 1
    if _positions(args, lambda t: t.startswith(DPCVARS)) != [last]:
        raise CommandError("-DPCVars= must be the last token, exactly once")
    if not max(zen) < trace < last:
        raise CommandError("the Zen tokens, then -notraceserver, must precede -DPCVars=")
    csv = _positions(args, lambda t: t.lower().startswith('-csv'))
    if pilot_launch:
        flags = [_exactly_once(args, flag) for flag in pilot.PILOT_CSV_FLAGS]
        if len(csv) != len(flags) or not max(flags) < min(zen):
            raise CommandError("a pilot render carries exactly the CSV tokens, before the Zen tokens")
    elif csv:
        raise CommandError("only a pilot render may carry -csv tokens")


def validate_args(args: Sequence[str], ctx: LaunchContext, pilot_launch: bool = False) -> None:
    """FR-UE-7 rules on a complete UE command line. Raises ``CommandError``."""
    if len(args) < 3:
        raise CommandError("a UE command line needs the editor, the project and switches")
    for index, token in enumerate(args):
        _check_token(index, token, ctx)
    _require_abs(args[1], "the .uproject path")
    if not args[1].lower().endswith('.uproject'):
        raise CommandError("args[1] must be the .uproject path")
    _check_order(args, ctx, pilot_launch)


def _finish(ctx: LaunchContext, phase: str, head: Sequence[str], merged, params_path,
            pilot_launch: bool = False) -> UeLaunch:
    """The single place every UE launch's tail and environment are built (req. 10, 11)."""
    if phase not in PHASES:
        raise CommandError(f"unknown phase {phase!r}")
    args = list(head)
    if pilot_launch:
        args += list(pilot.PILOT_CSV_FLAGS)
    args += list(zen_private.zen_flags(ctx.cache_dir, ctx.zen_port))
    args += list(user_state.isolation_args())
    args.append(dpcvars_token(merged))
    validate_args(args, ctx, pilot_launch)
    if params_path is not None:
        _require_abs(str(params_path), env_cleanup.UNREAL_PARAMS_VAR)
    env = env_cleanup.clean_env_for_unreal(None if params_path is None else str(params_path))
    env.update(user_state.isolation_env(ctx.profile))
    user_state.check_isolated_env(env, ctx.profile)
    return UeLaunch(phase=phase, pilot=pilot_launch, args=tuple(args), env=env, cwd=ctx.cache_dir,
                    profile=ctx.profile)


def build_prep(ctx: LaunchContext, uproject, script, log_path, params_path, merged) -> UeLaunch:
    """FR-UE-8: the PythonScript commandlet prep (0 VRAM, no window)."""
    head = [ctx.editor_cmd, str(uproject), '-run=pythonscript', f'-script={script}',
            '-unattended', '-nosplash', '-log', f'-abslog={log_path}']
    return _finish(ctx, 'prep', head, merged, params_path)


def build_warmup(ctx: LaunchContext, uproject, script, log_path, params_path, merged) -> UeLaunch:
    """FR-UE-9: the editor warm-up (``-ExecCmds="py <script>"``)."""
    head = [ctx.editor_cmd, str(uproject), '-unattended', '-nosplash', '-log', f'-abslog={log_path}',
            f'{EXEC_PY}{script}']
    return _finish(ctx, 'warmup', head, merged, params_path)


def build_render(ctx: LaunchContext, uproject, map_path: str, queue_asset: str, log_path, merged,
                 pilot_launch: bool = False) -> UeLaunch:
    """FR-UE-6: bench.ps1's render line (no audio, CSV only for a pilot)."""
    for value, what in ((map_path, 'map'), (queue_asset, 'queue asset')):
        try:
            validation.validate_object_path(value)
        except ValueError as exc:
            raise CommandError(f"invalid {what}: {exc}") from exc
    if map_path.startswith('-'):
        raise CommandError("the map must not start with '-'")
    head = [ctx.editor_cmd, str(uproject), map_path, '-game', f'-MoviePipelineConfig={queue_asset}',
            *farm_defaults.RENDER_FLAGS, f'-abslog={log_path}']
    return _finish(ctx, 'render', head, merged, None, pilot_launch=pilot_launch)
