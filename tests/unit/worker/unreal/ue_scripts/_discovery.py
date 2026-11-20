# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Runs the prep script in mode ``list_shots`` against the stub ``unreal`` module (shot discovery tests).

The params are the ones the worker writes (``sequence_prep.shots_params``), so the script is tested with exactly
what it gets in production.
"""
import json
import runpy
import sys

from sethlans_worker_agent.unreal_executor import job_dir, sequence_prep
from tests.unit.worker.unreal import _ue_stub as ue
from tests.unit.worker.unreal._executor_fakes import SETTINGS, params

SCRIPT = str(job_dir.SCRIPTS_SOURCE / job_dir.PREP_SCRIPT)
PRESET, SEQ = SETTINGS['unreal.preset'], SETTINGS['unreal.sequence']


def run_list(tmp_path, monkeypatch, preset, seq, scripts_dir=str(job_dir.SCRIPTS_SOURCE), max_shots=None):
    """``(result, error, world)`` of one ``list_shots`` prep."""
    world = {'assets': {PRESET: preset, SEQ: seq}}
    monkeypatch.setitem(sys.modules, 'unreal', ue.make_unreal(world))
    data = sequence_prep.shots_params(params(kind='shots', frame=0), tmp_path / 'result.json', scripts_dir)
    if max_shots is not None:
        data['max_shots'] = max_shots
    (tmp_path / 'params.json').write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('SETHLANS_UE_JOB_PARAMS', str(tmp_path / 'params.json'))
    error = None
    try:
        runpy.run_path(SCRIPT, run_name='sethlans_prep')
    except Exception as exc:  # the script re-raises whatever failed it
        error = exc
    return json.loads((tmp_path / 'result.json').read_text(encoding='utf-8')), error, world


def shots_of(tmp_path, monkeypatch, tracks, start=0, end=1000):
    """``(shot_source, shots, warnings)`` of a sequence with these top-level tracks, or the error text."""
    result, error, _world = run_list(tmp_path, monkeypatch, ue.graph(), ue.Sequence(start, end, tracks))
    if error is not None:
        assert result['ok'] is False and 'shots' not in result
        return result['error']
    return result['shot_source'], result['shots'], result['warnings']
