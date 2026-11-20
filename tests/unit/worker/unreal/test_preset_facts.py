# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What a render ran with (GPU memory reader D23, FR-GC-1..6, FR-GT-11, AC-23, AC-24): the UE-side facts
module against a stub ``unreal`` module, the static never-writes checks, the worker's parsing and summary.
"""
import ast
import copy
import importlib.util
import inspect
import json
import re
import sys
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import farm_defaults, job_dir, job_flow, pilot, queue_prep
from sethlans_worker_agent.unreal_executor.summary import Summary
from shared.unreal import render_context
from tests.unit.worker.unreal import _ue_stub as ue
from tests.unit.worker.unreal._executor_fakes import params

FACTS_PATH = job_dir.SCRIPTS_SOURCE / job_dir.FACTS_SCRIPT
PREP_PATH = job_dir.SCRIPTS_SOURCE / job_dir.PREP_SCRIPT
STREAMING_CVAR = re.compile(r'(?i)texturestreaming|r\.streaming\.')


@pytest.fixture()
def facts(monkeypatch):
    monkeypatch.setitem(sys.modules, 'unreal', ue.make_unreal({'assets': {}}))
    spec = importlib.util.spec_from_file_location('sethlans_preset_facts_test', FACTS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _graph(*nodes):
    return ue.graph(overrides=list(nodes))


class TestGraph:

    @pytest.mark.parametrize('nodes,expected', [
        ((), (4, 'none', 'unchanged')),                                              # class defaults
        ((ue.overrides_node(scalability=2, streaming=True),), (2, 'preset', 'off')),
        ((ue.overrides_node(scalability=3, streaming=False),), (3, 'preset', 'unchanged')),
        ((ue.overrides_node(),), (4, 'preset', 'unchanged')),                        # a node that overrides nothing
        ((ue.overrides_node(scalability=1, streaming=True, disabled=True),), (4, 'none', 'unchanged')),
        ((ue.overrides_node(scalability=1, streaming=True, disabled=True), ue.overrides_node(scalability=3)),
         (3, 'preset', 'unchanged')),                                                # the disabled node is skipped
        ((ue.overrides_node(scalability=2, streaming=True), ue.overrides_node(scalability=2, streaming=True)),
         (2, 'preset', 'off')),                                                      # nodes that agree
        ((ue.overrides_node(scalability=2), ue.overrides_node(streaming=True)), (2, 'preset', 'off')),
    ])
    def test_values(self, facts, nodes, expected):
        warnings = []
        graph = _graph(*nodes)
        assert (facts.graph_scalability(graph, warnings),) + facts.game_overrides(graph, 'graph', warnings) == expected
        assert warnings == []

    @pytest.mark.parametrize('nodes', [
        (ue.overrides_node(scalability=2), ue.overrides_node(scalability=3)),
        (ue.overrides_node(streaming=True), ue.overrides_node(streaming=False)),
    ])
    def test_nodes_that_disagree_give_4_unknown_and_one_warning(self, facts, nodes):
        warnings = []
        graph = _graph(*nodes)
        assert facts.graph_scalability(graph, warnings) == 4
        assert facts.game_overrides(graph, 'graph', warnings) == ('unknown', 'unknown')
        assert warnings == [facts.CONFLICT]  # both calls see it; one entry

    def test_an_unreadable_graph_gives_unknown_and_a_warning_without_the_message(self, facts):
        graph = _graph(ue.overrides_node(scalability=2))

        def fail():
            raise OSError(r'C:\Users\artist\Projects\Secret\Content\Presets\MRG.uasset is locked')
        graph.get_branch_names = fail
        warnings = []
        assert facts.game_overrides(graph, 'graph', warnings) == ('unknown', 'unknown')
        assert facts.graph_scalability(graph, warnings) == 4
        assert warnings == ["The preset's Game Overrides were not read (OSError).",
                            "The preset's scalability was not read (OSError); Cinematic is used."]

    @pytest.mark.parametrize('extra', [(), (ue.overrides_node(scalability=2, streaming=True),)])
    def test_an_enabled_subgraph_gives_4_unknown_and_one_warning(self, facts, extra):
        """Its own Game Overrides nodes are not read, so nothing is claimed about them."""
        graph = _graph(ue.MovieGraphSubgraphNode(), *extra)
        warnings = []
        assert facts.graph_scalability(graph, warnings) == 4
        assert facts.game_overrides(graph, 'graph', warnings) == ('unknown', 'unknown')
        assert warnings == [facts.SUBGRAPH]

    def test_a_disabled_subgraph_is_skipped(self, facts):
        graph = _graph(ue.MovieGraphSubgraphNode(disabled=True), ue.overrides_node(scalability=2, streaming=True))
        warnings = []
        assert (facts.graph_scalability(graph, warnings),) + facts.game_overrides(graph, 'graph', warnings) == \
            (2, 'preset', 'off')
        assert warnings == []


class TestLegacy:
    METHOD = ue.MoviePipelineTextureStreamingMethod

    @pytest.mark.parametrize('settings,expected', [
        ((), ('implicit', 'off')),                       # MRQ adds its own: streaming disabled
        ((ue.MoviePipelineGameOverrideSetting(texture_streaming=METHOD.DISABLED),), ('preset', 'off')),
        ((ue.MoviePipelineGameOverrideSetting(texture_streaming=METHOD.FULLY_LOAD),), ('preset', 'fully_load')),
        ((ue.MoviePipelineGameOverrideSetting(texture_streaming=METHOD.NONE),), ('preset', 'unchanged')),
        ((ue.MoviePipelineGameOverrideSetting(texture_streaming='OTHER'),), ('preset', 'unknown')),
        # Present but disabled: blocks MRQ's implicit one, applies nothing (MoviePipelinePrimaryConfig.cpp:33-60).
        ((ue.MoviePipelineGameOverrideSetting(texture_streaming=METHOD.DISABLED, disabled=True),), ('none', 'unchanged')),
    ])
    def test_values(self, facts, settings, expected):
        warnings = []
        assert facts.game_overrides(ue.MoviePipelinePrimaryConfig(settings), 'legacy', warnings) == expected
        assert warnings == []

    def test_an_unreadable_preset_or_kind_gives_unknown(self, facts):
        warnings = []
        assert facts.game_overrides(object(), 'legacy', warnings) == ('unknown', 'unknown')
        assert facts.game_overrides(ue.MoviePipelinePrimaryConfig(), 'video', warnings) == ('unknown', 'unknown')
        assert warnings == ["The preset's Game Overrides were not read (AttributeError)."]  # class name, no message


def test_reading_never_changes_the_preset(facts):
    graph = _graph(ue.overrides_node(scalability=2, streaming=True), ue.overrides_node(disabled=True))
    legacy = ue.MoviePipelinePrimaryConfig([ue.MoviePipelineGameOverrideSetting(texture_streaming='DISABLED')])
    before = copy.deepcopy((graph.branches, legacy.settings))
    facts.graph_scalability(graph, [])
    facts.game_overrides(graph, 'graph', [])
    facts.game_overrides(legacy, 'legacy', [])
    assert [[n.__dict__ for n in nodes] for nodes in graph.branches.values()] == \
        [[n.__dict__ for n in nodes] for nodes in before[0].values()]
    assert [s.__dict__ for s in legacy.settings] == [s.__dict__ for s in before[1]]


class TestStatic:

    def test_the_facts_module_never_writes_to_the_preset(self):
        """AC-24: no property set, no setting added or toggled, no save."""
        text = FACTS_PATH.read_text(encoding='utf-8')
        for call in ('set_editor_property', 'find_or_add_setting_by_class', 'save_loaded_asset', 'set_is_enabled',
                     'set_disabled', 'add_setting'):
            assert call not in text, call
        imported = [alias.name for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.Import, ast.ImportFrom))
                    for alias in n.names]
        assert imported == ['unreal']

    def test_the_prep_loads_it_by_file_path_and_never_touches_sys_path(self):
        text = PREP_PATH.read_text(encoding='utf-8')
        assert 'importlib.util.spec_from_file_location(FACTS, os.path.join(P["scripts_dir"], FACTS + ".py"))' in text
        tree = ast.parse(text)
        attributes = {(n.value.id, n.attr) for n in ast.walk(tree)
                      if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
        assert ('sys', 'path') not in attributes and ('sys', 'modules') not in attributes
        imported = {alias.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                    for alias in n.names}
        assert imported == {'importlib.util', 'json', 'os', 'sys', 'time', 'unreal'}
        assert '__import__' not in text and 'import_module' not in text

    def test_no_streaming_cvar_of_the_workers_own(self):
        """FR-GC-6, AC-24: the farm defaults (every launch's CVars and flags) hold no texture-streaming CVar."""
        values = [*farm_defaults.FARM_CVARS, *farm_defaults.FARM_CRITICAL_CVARS, *farm_defaults.AUDIO_CVARS,
                  *farm_defaults.AUDIO_FLAGS, *farm_defaults.RENDER_FLAGS, *farm_defaults.HEADLESS_FLAGS,
                  *farm_defaults.NETWORK_FLAGS]
        assert not [v for v in values if STREAMING_CVAR.search(v)]


class TestWorkerSide:

    @pytest.mark.parametrize('go', queue_prep.GAME_OVERRIDES)
    @pytest.mark.parametrize('ts', queue_prep.TEXTURE_STREAMING)
    def test_every_allowed_word_is_kept(self, go, ts):
        prep = _parse(game_overrides=go, texture_streaming=ts)
        assert (prep.game_overrides, prep.texture_streaming) == (go, ts)

    @pytest.mark.parametrize('value', [None, '', 'OFF', 'preset ', 'disabled', 4, True, ['off'], {'a': 1}, 'x' * 5000])
    def test_absent_or_invalid_is_unknown_and_the_job_goes_on(self, value):
        changes = {} if value is None else {'game_overrides': value, 'texture_streaming': value}
        prep = _parse(**changes)
        assert (prep.game_overrides, prep.texture_streaming) == ('unknown', 'unknown')
        assert prep.queue_asset.endswith('Q_Job42')

    def test_the_allowlists_come_from_the_one_shared_home(self):
        assert render_context.GAME_OVERRIDES_VALUES == ('implicit', 'none', 'preset', 'unknown')
        assert render_context.TEXTURE_STREAMING_VALUES == ('fully_load', 'off', 'unchanged', 'unknown')
        for values in (render_context.GAME_OVERRIDES_VALUES, render_context.TEXTURE_STREAMING_VALUES):
            assert list(values) == sorted(values) and render_context.UNKNOWN in values
        assert queue_prep.GAME_OVERRIDES is render_context.GAME_OVERRIDES_VALUES
        assert queue_prep.TEXTURE_STREAMING is render_context.TEXTURE_STREAMING_VALUES
        defaults = inspect.signature(pilot.report_body).parameters
        assert defaults['game_overrides'].default == defaults['texture_streaming'].default == render_context.UNKNOWN

    def test_the_implicit_warning_is_brand_neutral_and_names_streaming_off_and_the_next_step(self):
        assert queue_prep.IMPLICIT_WARNING.endswith(
            "To choose, add a Game Overrides setting to the preset and set its Texture Streaming.")
        text = queue_prep.IMPLICIT_WARNING.lower()
        assert 'texture streaming off' in text and 'game overrides' in text
        assert not any(brand in text for brand in ('nvidia', 'amd', 'radeon', 'geforce', 'intel'))


class TestSummary:
    """FR-GC-5, AC-23, AC-32: the job summary carries both values next to ``preset_kind``; ``implicit`` warns once."""

    def _prep(self, tmp_path, monkeypatch, **changes):
        job = SimpleNamespace(params=tmp_path / 'params.json', prep_result=tmp_path / 'prep_result.json',
                              output=tmp_path / 'output', scripts=tmp_path / 'scripts',
                              script=lambda name: tmp_path / 'scripts' / name,
                              log=lambda phase, attempt: tmp_path / f'{phase}.{attempt}.log')
        data = {'ok': True, 'queue_asset': '/Game/_SethlansFarm/Job42/Q_Job42', 'preset_kind': 'legacy',
                'playback_range': [0, 1928], 'camera_cut_starts': [0], 'effective_cvars': {}, 'warnings': []}
        data.update(changes)
        job.prep_result.write_text(json.dumps(data), encoding='utf-8')
        outcome = SimpleNamespace(wall_s=1.0, exit_code=0, quit_forced=False, fatal_rule=None, rule_counts={})
        runner = SimpleNamespace(launch=lambda *args, **kwargs: SimpleNamespace(outcome=outcome))
        monkeypatch.setattr(job_flow.render_check, 'check_prep', lambda outcome: None)
        monkeypatch.setattr(job_flow.cvars, 'check_effective', lambda effective, merged: ('', []))
        summary = Summary()
        flow = job_flow.UeFlow(params(), SimpleNamespace(), job, runner, summary,
                               SimpleNamespace(build_prep=lambda *args: 'launch'), None, 'Demo.uproject',
                               SimpleNamespace())
        prep = flow.prep()
        assert json.loads(job.params.read_text(encoding='utf-8'))['scripts_dir'] == str(job.scripts)
        return prep, summary

    def test_implicit_carries_the_values_and_one_warning(self, tmp_path, monkeypatch):
        prep, summary = self._prep(tmp_path, monkeypatch, game_overrides='implicit', texture_streaming='off')
        assert (prep.game_overrides, prep.texture_streaming) == ('implicit', 'off')
        lines = summary.text().splitlines()
        kind = lines.index('[sethlans-unreal] preset_kind=legacy')
        assert lines[kind + 1:kind + 3] == ['[sethlans-unreal] game_overrides=implicit',
                                            '[sethlans-unreal] texture_streaming=off']
        assert list(summary.warnings()).count(queue_prep.IMPLICIT_WARNING) == 1 and len(summary.warnings()) == 1

    @pytest.mark.parametrize('changes,expected', [
        ({'game_overrides': 'preset', 'texture_streaming': 'fully_load'}, ('preset', 'fully_load')),
        ({'game_overrides': 'none', 'texture_streaming': 'unchanged'}, ('none', 'unchanged')),
        ({'game_overrides': 'unknown', 'texture_streaming': 'unknown'}, ('unknown', 'unknown')),
        ({}, ('unknown', 'unknown')),                                       # absent: the job goes on
        ({'game_overrides': 'IMPLICIT', 'texture_streaming': 'off '}, ('unknown', 'unknown')),
    ])
    def test_no_other_value_warns(self, tmp_path, monkeypatch, changes, expected):
        _prep, summary = self._prep(tmp_path, monkeypatch, **changes)
        assert (summary.get('game_overrides'), summary.get('texture_streaming')) == expected
        assert list(summary.warnings()) == []


def _parse(**changes):
    data = {'ok': True, 'queue_asset': '/Game/_SethlansFarm/Job42/Q_Job42', 'preset_kind': 'graph',
            'playback_range': [0, 1928], 'camera_cut_starts': [0], 'effective_cvars': {}, 'warnings': []}
    data.update(changes)
    return queue_prep.parse_result(data, '/Game/_SethlansFarm/Job42', False, '/Game/Seq/Edit')
