# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The UE-side edits module (``ue_scripts/sethlans_sequence_edits.py``, sequence rendering FR-SW-4): data that
imports only ``unreal`` and never saves an asset; the prep loads it from ``scripts_dir`` by file path (never
through ``sys.path``) and fails loudly without it; the worker ships it.
"""
import ast
import json
import shutil
import sys
from pathlib import Path

import pytest

from sethlans_worker_agent.unreal_executor import job_dir
from tests.unit.worker.unreal.test_ue_scripts import poc_assets, run

REPO = Path(__file__).resolve().parents[5]
EDITS_PATH = job_dir.SCRIPTS_SOURCE / job_dir.EDITS_SCRIPT
PREP_PATH = job_dir.SCRIPTS_SOURCE / job_dir.PREP_SCRIPT


def test_it_imports_only_unreal_and_never_saves_or_duplicates():
    text = EDITS_PATH.read_text(encoding='utf-8')
    tree = ast.parse(text)
    imported = [alias.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for alias in n.names]
    assert imported == ['unreal']
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {'sys', '__import__', 'exec', 'eval', 'open'}
    for call in ('save_loaded_asset', 'duplicate_asset', 'save_asset'):
        assert call not in text, call


def test_the_prep_loads_it_by_file_path():
    text = PREP_PATH.read_text(encoding='utf-8')
    assert 'EDITS = "sethlans_sequence_edits"' in text and 'load_required(EDITS, "edits", "EditsError")' in text
    assert 'importlib.util.spec_from_file_location(name, os.path.join(P["scripts_dir"], name + ".py"))' in text


def test_it_is_shipped_with_the_other_ue_scripts():
    assert EDITS_PATH.is_file() and job_dir.EDITS_SCRIPT in job_dir.UE_SCRIPTS
    spec = (REPO / 'packaging' / 'pyinstaller' / 'worker.spec').read_text(encoding='utf-8')
    assert "'sethlans_sequence_edits.py'" in spec


def _scripts(tmp_path, without=None):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in (job_dir.FACTS_SCRIPT, job_dir.SHOTS_SCRIPT, job_dir.EDITS_SCRIPT):
        if name != without:
            shutil.copyfile(job_dir.SCRIPTS_SOURCE / name, scripts / name)
    return scripts


@pytest.mark.parametrize('broken,cls', [(False, 'FileNotFoundError'), (True, 'SyntaxError')])
def test_an_edits_module_that_does_not_load_fails_the_prep_without_its_path(tmp_path, monkeypatch, broken, cls):
    scripts = _scripts(tmp_path, without=None if broken else job_dir.EDITS_SCRIPT)
    if broken:
        (scripts / job_dir.EDITS_SCRIPT).write_text('def (\n', encoding='utf-8')
    result, error, world = run(tmp_path, monkeypatch, poc_assets(), scripts_dir=str(scripts))
    assert isinstance(error, RuntimeError) and result['error'] == f'The edits module did not load ({cls}).'
    assert str(tmp_path) not in json.dumps(result) and not world['queues']


def test_an_edits_error_is_reported_with_its_own_text(tmp_path, monkeypatch):
    result, error, _world = run(tmp_path, monkeypatch, poc_assets(png=False))
    assert error is not None and result['error'] == 'Preset does not output PNG; phase 1 does not add outputs.'


def test_a_missing_unreal_name_fails_the_prep_naming_it(tmp_path, monkeypatch):
    """No silent fallback (#283): a class this Unreal lacks fails the prep with the missing name only."""
    from tests.unit.worker.unreal import _ue_stub
    make = _ue_stub.make_unreal

    def without_layers(world):
        module = make(world)
        del module.MovieGraphRenderLayerNode
        return module
    monkeypatch.setattr(_ue_stub, 'make_unreal', without_layers)
    result, error, _world = run(tmp_path, monkeypatch, poc_assets())
    assert isinstance(error, AttributeError)
    assert result['error'] == 'Unreal prep pass failed (AttributeError: no MovieGraphRenderLayerNode).'


def test_a_sethlans_sequence_edits_on_sys_path_is_never_imported(tmp_path, monkeypatch):
    planted = tmp_path / 'planted'
    planted.mkdir()
    (planted / 'sethlans_sequence_edits.py').write_text("raise SystemExit('planted module ran')\n", encoding='utf-8')
    monkeypatch.syspath_prepend(str(planted))
    result, error, _world = run(tmp_path, monkeypatch, poc_assets())
    assert error is None and result['ok'] is True and 'sethlans_sequence_edits' not in sys.modules
