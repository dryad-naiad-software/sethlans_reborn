# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The UE-side shots module (``ue_scripts/sethlans_shots.py``, sequence rendering FR-SW-4): the prep script
loads it from ``scripts_dir`` by file path (never through ``sys.path``), it fails the prep loudly when it
does not load, and the worker ships it (job directory and PyInstaller lists).
"""
import ast
import json
import shutil
import sys
from pathlib import Path

import pytest

from sethlans_worker_agent.unreal_executor import job_dir
from tests.unit.worker.unreal.test_ue_scripts import poc_assets, run

REPO = Path(__file__).resolve().parents[4]
SHOTS_PATH = job_dir.SCRIPTS_SOURCE / job_dir.SHOTS_SCRIPT
PREP_PATH = job_dir.SCRIPTS_SOURCE / job_dir.PREP_SCRIPT
HELPERS = {'rate', 'active', 'shot_sections', 'cuts_of', 'in_shot_cut', 'cut_starts', 'frame_in_cut', 'pilot_frames'}


def _functions(path):
    return {n.name for n in ast.walk(ast.parse(path.read_text(encoding='utf-8'))) if isinstance(n, ast.FunctionDef)}


def _scripts_copy(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in (job_dir.FACTS_SCRIPT, job_dir.SHOTS_SCRIPT):
        shutil.copyfile(job_dir.SCRIPTS_SOURCE / name, scripts / name)
    return scripts


class TestStatic:

    def test_the_prep_loads_it_by_file_path_and_keeps_no_copy_of_the_helpers(self):
        text = PREP_PATH.read_text(encoding='utf-8')
        assert 'SHOTS = "sethlans_shots"' in text
        assert 'importlib.util.spec_from_file_location(SHOTS, os.path.join(P["scripts_dir"], SHOTS + ".py"))' in text
        assert _functions(SHOTS_PATH) == HELPERS
        assert not _functions(PREP_PATH) & HELPERS

    def test_the_shots_module_imports_only_unreal_and_never_writes(self):
        text = SHOTS_PATH.read_text(encoding='utf-8')
        tree = ast.parse(text)
        imported = [alias.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for alias in n.names]
        assert imported == ['unreal']
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert 'sys' not in names and '__import__' not in names
        for call in ('save_loaded_asset', 'set_editor_property', 'duplicate_asset', 'set_disabled', 'set_is_enabled'):
            assert call not in text, call

    def test_it_is_shipped_with_the_other_ue_scripts(self):
        assert SHOTS_PATH.is_file() and job_dir.SHOTS_SCRIPT == 'sethlans_shots.py'
        assert job_dir.SHOTS_SCRIPT in job_dir.UE_SCRIPTS
        spec = (REPO / 'packaging' / 'pyinstaller' / 'worker.spec').read_text(encoding='utf-8')
        assert "'sethlans_shots.py'" in spec


class TestLoading:

    def test_the_copy_in_scripts_dir_is_the_one_used(self, tmp_path, monkeypatch):
        scripts = _scripts_copy(tmp_path)
        shots = scripts / job_dir.SHOTS_SCRIPT
        shots.write_text(shots.read_text(encoding='utf-8') + '\n\ndef cut_starts(seq):\n    return [7]\n',
                         encoding='utf-8')
        result, error, _world = run(tmp_path, monkeypatch, poc_assets(), scripts_dir=str(scripts))
        assert error is None and result['ok'] is True and result['camera_cut_starts'] == [7]
        assert 'sethlans_shots' not in sys.modules

    def test_a_sethlans_shots_on_sys_path_is_never_imported(self, tmp_path, monkeypatch):
        planted = tmp_path / 'planted'
        planted.mkdir()
        (planted / 'sethlans_shots.py').write_text("raise SystemExit('planted module ran')\n", encoding='utf-8')
        monkeypatch.syspath_prepend(str(planted))
        result, error, _world = run(tmp_path, monkeypatch, poc_assets())
        assert error is None and result['ok'] is True and result['camera_cut_starts'] == [965]
        assert 'sethlans_shots' not in sys.modules

    @pytest.mark.parametrize('where,cls', [('missing', 'KeyError'), ('absent', 'FileNotFoundError'),
                                           ('broken', 'SyntaxError')])
    def test_a_shots_module_that_does_not_load_fails_the_prep_without_its_path(self, tmp_path, monkeypatch, where, cls):
        scripts = _scripts_copy(tmp_path)
        if where == 'absent':
            (scripts / job_dir.SHOTS_SCRIPT).unlink()
        elif where == 'broken':
            (scripts / job_dir.SHOTS_SCRIPT).write_text('def (\n', encoding='utf-8')
        result, error, world = run(tmp_path, monkeypatch, poc_assets(),
                                   scripts_dir=None if where == 'missing' else str(scripts))
        assert isinstance(error, RuntimeError) and result['ok'] is False
        assert result['error'] == f'The shots module did not load ({cls}).'
        assert str(tmp_path) not in json.dumps(result) and 'queue_asset' not in result and not world['queues']
