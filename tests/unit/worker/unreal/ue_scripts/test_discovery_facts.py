# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shot discovery's preset facts on the UE side (sequence rendering FR-SD-5, D5b, D6, D7, U-1): what the prep in
mode ``list_shots`` reports about a graph or legacy preset, read only, against the stub ``unreal`` module.
"""
import copy
import json
import shutil

import pytest

from sethlans_worker_agent.unreal_executor import job_dir, sequence_prep
from shared.unreal import shots_result
from tests.unit.worker.unreal import _ue_stub as ue, _ue_stub_seq
from tests.unit.worker.unreal.ue_scripts._discovery import PRESET, run_list

SEQ = ue.Sequence(0, 240, [ue.MovieSceneCameraCutTrack([ue.Section(0, 240)])], rate=(30000, 1001))
GRAPH_DEFAULTS = {'resolution': [1920, 1080], 'custom_frame_rate': None, 'frame_step': None, 'handle_frames': 0,
                  'frame_number_offset': 0, 'zero_pad': 4, 'temporal_samples': 1, 'spatial_samples': 1,
                  'layers_ok': True, 'can_png': True, 'can_exr': False, 'can_sound': False, 'game_overrides': 'none',
                  'texture_streaming': 'unchanged'}


def facts_of(tmp_path, monkeypatch, preset):
    result, error, _world = run_list(tmp_path, monkeypatch, preset, copy.deepcopy(SEQ))
    assert error is None and result['ok'] is True, result
    assert set(result['preset_facts']) == set(shots_result.FACT_KEYS)  # exactly the keys both sides accept
    sequence_prep.parse_shots(result)  # and the worker's strict check passes
    return result['preset_facts']


def output_node(**props):
    return ue.MovieGraphGlobalOutputSettingNode(**props)


def graph_with(globals_nodes=(), layer_nodes=(), **graph_kw):
    preset = ue.graph(**graph_kw)
    preset.branches['Globals'] = list(globals_nodes) or preset.branches['Globals']
    preset.branches['DefaultLayer'].extend(layer_nodes)
    return preset


class TestGraph:

    def test_class_defaults_and_an_absent_frame_step(self, tmp_path, monkeypatch):
        """U-1: the 5.8.4 node has no frame-step property, so it is reported absent (None)."""
        assert facts_of(tmp_path, monkeypatch, ue.graph()) == GRAPH_DEFAULTS

    def test_overridden_output_settings_samples_and_outputs(self, tmp_path, monkeypatch):
        node = output_node(override_output_resolution=True,
                           output_resolution=ue.MovieGraphNamedResolution('Custom', ue.size(3840, 2160)),
                           override_output_frame_rate=True, output_frame_rate=ue.FrameRate(25),
                           override_zero_pad_frame_numbers=True, zero_pad_frame_numbers=6,
                           override_handle_frame_count=True, handle_frame_count=5,
                           override_frame_number_offset=True, frame_number_offset=100)
        sampling = ue.MovieGraphSamplingMethodNode(override_temporal_sample_count=True, temporal_sample_count=8)
        unset = ue.MovieGraphSamplingMethodNode(override_temporal_sample_count=False, temporal_sample_count=64)
        pass_node = ue.MovieGraphDeferredRenderPassNode(override_spatial_sample_count=True, spatial_sample_count=4)
        preset = graph_with([node, sampling, unset], [ue.MovieGraphImageSequenceOutputNode_EXR(disabled=True),
                                                      ue.MovieGraphAudioOutputNode()], passes=0)
        preset.branches['DefaultLayer'].append(pass_node)
        facts = facts_of(tmp_path, monkeypatch, preset)
        assert facts == dict(GRAPH_DEFAULTS, resolution=[3840, 2160], custom_frame_rate=[25, 1], zero_pad=6,
                             handle_frames=5, frame_number_offset=100, temporal_samples=8, spatial_samples=4,
                             can_exr=True, can_sound=True)  # a disabled EXR node still counts (D6)

    def test_a_value_set_without_its_override_flag_is_not_used(self, tmp_path, monkeypatch):
        node = output_node(output_frame_rate=ue.FrameRate(60), zero_pad_frame_numbers=9)
        assert facts_of(tmp_path, monkeypatch, graph_with([node])) == GRAPH_DEFAULTS

    def test_a_disabled_node_is_skipped_and_layers_are_reported_not_refused(self, tmp_path, monkeypatch):
        off = output_node(override_zero_pad_frame_numbers=True, zero_pad_frame_numbers=9, disabled=True)
        facts = facts_of(tmp_path, monkeypatch, graph_with([output_node(), off], layers=2, png=False))
        assert facts == dict(GRAPH_DEFAULTS, layers_ok=False, can_png=False)

    def test_nodes_that_disagree_fail_the_discovery(self, tmp_path, monkeypatch):
        nodes = [output_node(override_output_frame_rate=True, output_frame_rate=ue.FrameRate(24)),
                 output_node(override_output_frame_rate=True, output_frame_rate=ue.FrameRate(25))]
        result, error, _world = run_list(tmp_path, monkeypatch, graph_with(nodes), copy.deepcopy(SEQ))
        assert error is not None and result['ok'] is False
        assert result['error'] == ("The preset's Global Output Settings nodes disagree on output_frame_rate; the "
                                   "render's output is unknown.")

    @pytest.mark.parametrize('exposed,refused', [
        (['ZeroPadFrameNumbers'], 'zero_pad_frame_numbers'), (['OutputFrameRate'], 'output_frame_rate'),
        (['OutputResolution', 'OutputDirectory'], 'output_resolution'), (['OutputDirectory'], None), ([], None),
    ])
    def test_a_d5b_value_exposed_as_a_pin_fails_the_discovery(self, tmp_path, monkeypatch, exposed, refused):
        """Domain should-fix 2: a promoted property renders with what feeds it, not the stored value."""
        node = output_node()
        node.get_exposed_properties = lambda: [ue.types.SimpleNamespace(name=name) for name in exposed]
        result, error, _world = run_list(tmp_path, monkeypatch, graph_with([node]), copy.deepcopy(SEQ))
        if refused is None:
            assert error is None and result['preset_facts'] == GRAPH_DEFAULTS
        else:
            assert result['error'] == (f"The preset's Global Output Settings expose {refused} as a pin or variable; "
                                       "Sethlans cannot read the value that renders.")


class TestRenderedSize:
    """Issue #283: UE renders a known profile's size, not the size stored beside it."""

    def _resolution(self, tmp_path, monkeypatch, named):
        node = output_node(override_output_resolution=True, output_resolution=named)
        return facts_of(tmp_path, monkeypatch, graph_with([node]))['resolution']

    def test_a_known_profile_wins_over_a_stale_stored_size(self, tmp_path, monkeypatch):
        named = ue.MovieGraphNamedResolution('1080p (FHD)', ue.size(128, 72))
        assert ue.resolved_size(named) == (1920, 1080)
        assert self._resolution(tmp_path, monkeypatch, named) == [1920, 1080]

    def test_another_known_profile_and_an_unknown_name(self, tmp_path, monkeypatch):
        assert self._resolution(tmp_path, monkeypatch, ue.MovieGraphNamedResolution('720p (HD)', ue.size(64, 64))) \
            == [1280, 720]
        assert self._resolution(tmp_path, monkeypatch, ue.MovieGraphNamedResolution('Studio', ue.size(2048, 858))) \
            == [2048, 858]

    def test_without_the_library_the_discovery_fails_loudly(self, tmp_path, monkeypatch):
        """No silent fallback (#283): a missing ``unreal.MovieGraphLibrary`` fails with its name, never a guess."""
        monkeypatch.setattr(_ue_stub_seq, 'size_library', lambda: ue.types.SimpleNamespace())
        node = output_node(override_output_resolution=True,
                           output_resolution=ue.MovieGraphNamedResolution('Custom', ue.size(640, 360)))
        result, error, _world = run_list(tmp_path, monkeypatch, graph_with([node]), copy.deepcopy(SEQ))
        assert error is not None and result['error'] == ("The preset's output settings were not read (AttributeError: "
                                                         "no is_named_resolution_valid).")

    def test_a_missing_property_names_itself(self, tmp_path, monkeypatch):
        node = output_node()
        del node.props['override_zero_pad_frame_numbers']
        result, error, _world = run_list(tmp_path, monkeypatch, graph_with([node]), copy.deepcopy(SEQ))
        assert error is not None and result['error'] == ("Unreal has no property override_zero_pad_frame_numbers on "
                                                         "MovieGraphGlobalOutputSettingNode; the preset's output "
                                                         "settings were not read.")


ADAPT = ("The preset adapts its output resolution to the camera; a shot whose camera constrains its aspect ratio "
         "renders at another size than the one set, which Sethlans then refuses.")


@pytest.mark.parametrize('nodes,warned', [
    ([], False),  # the class default: Disabled
    ([{'override_adapt_resolution': True, 'adapt_resolution': 'AUTOMATIC'}], True),
    ([{'override_adapt_resolution': False, 'adapt_resolution': 'AUTOMATIC'}], False),  # not overridden: Disabled
    ([{'override_adapt_resolution': True, 'adapt_resolution': 'AUTOMATIC', 'disabled': True}], False),
])
def test_an_adapted_resolution_warns_in_discovery(tmp_path, monkeypatch, nodes, warned):
    """#283 review: UE resizes the output to a constrained camera's aspect ratio unless adapting is Disabled."""
    preset = graph_with([output_node(**props) for props in nodes])
    result, error, _world = run_list(tmp_path, monkeypatch, preset, copy.deepcopy(SEQ))
    assert error is None and (ADAPT in result['warnings']) is warned
    assert result['preset_facts']['resolution'] == [1920, 1080]


class TestErrorText:
    """Security review: an exception the scripts did not compose is reported by class name only (it can carry a
    local path); text they composed is reported as it is."""

    class Broken(ue.Sequence):
        def get_playback_start(self):
            raise OSError(r'C:\Users\artist\Projects\Secret\Content\Edit.uasset is locked')

    def test_discovery(self, tmp_path, monkeypatch):
        result, error, _world = run_list(tmp_path, monkeypatch, ue.graph(), self.Broken(0, 10))
        assert isinstance(error, OSError) and result['error'] == 'Unreal prep pass failed (OSError).'
        assert 'artist' not in json.dumps(result)

    def test_the_frame_prep_too(self, tmp_path, monkeypatch):
        from tests.unit.worker.unreal.test_ue_scripts import PRESET as P, SEQ as S, run
        result, error, _world = run(tmp_path, monkeypatch, {P: ue.graph(), S: self.Broken(0, 10)})
        assert isinstance(error, OSError) and result['error'] == 'Unreal prep pass failed (OSError).'
        result, error, _world = run(tmp_path, monkeypatch, {})
        assert result['error'] == f'Preset {P} not found.'  # composed text stays as it is


class TestLegacy:

    def test_the_output_setting_samples_and_outputs(self, tmp_path, monkeypatch):
        out = ue.MoviePipelineOutputSetting(output_resolution=ue.size(1280, 720), use_custom_frame_rate=True,
                                            output_frame_rate=ue.FrameRate(30000, 1001), handle_frame_count=2,
                                            output_frame_step=2, frame_number_offset=10, zero_pad_frame_numbers=5)
        aa = ue.MoviePipelineAntiAliasingSetting(temporal_sample_count=4, spatial_sample_count=2)
        preset = ue.MoviePipelinePrimaryConfig([out, aa, ue.MoviePipelineImageSequenceOutput_PNG(),
                                                ue.MoviePipelineRenderPass()])
        assert facts_of(tmp_path, monkeypatch, preset) == {
            'resolution': [1280, 720], 'custom_frame_rate': [30000, 1001], 'frame_step': 2, 'handle_frames': 2,
            'frame_number_offset': 10, 'zero_pad': 5, 'temporal_samples': 4, 'spatial_samples': 2, 'layers_ok': True,
            'can_png': True, 'can_exr': True, 'can_sound': True,  # the farm's duplicate adds EXR and wave (D6, D7)
            'game_overrides': 'implicit', 'texture_streaming': 'off'}

    def test_no_settings_give_the_class_defaults(self, tmp_path, monkeypatch):
        preset = ue.MoviePipelinePrimaryConfig([ue.MoviePipelineRenderPass(), ue.MoviePipelineRenderPass(),
                                                ue.MoviePipelineAntiAliasingSetting(disabled=True,
                                                                                    temporal_sample_count=8)])
        facts = facts_of(tmp_path, monkeypatch, preset)
        assert (facts['resolution'], facts['custom_frame_rate'], facts['frame_step'], facts['zero_pad']) == \
            ([1920, 1080], None, 1, 4)
        assert (facts['temporal_samples'], facts['spatial_samples']) == (1, 1)  # a disabled AA setting applies nothing
        assert facts['layers_ok'] is False and facts['can_png'] is False

    def test_an_unreadable_preset_fails_with_fixed_text_and_the_class_name(self, tmp_path, monkeypatch):
        preset = ue.MoviePipelinePrimaryConfig()

        def broken(*args, **kwargs):
            raise OSError(r'C:\Users\artist\Projects\Secret\Content\Presets\MRQ.uasset is locked')
        preset.find_setting_by_class = broken
        result, error, _world = run_list(tmp_path, monkeypatch, preset, copy.deepcopy(SEQ))
        assert error is not None and result['error'] == "The preset's output settings were not read (OSError)."
        assert 'artist' not in json.dumps(result)


def test_reading_never_changes_the_preset(tmp_path, monkeypatch):
    preset = graph_with([output_node(override_zero_pad_frame_numbers=True, zero_pad_frame_numbers=6)])
    before = copy.deepcopy(preset)
    result, _error, world = run_list(tmp_path, monkeypatch, preset, copy.deepcopy(SEQ))
    assert result['ok'] is True and world['assets'][PRESET] is preset and world['saved'] == []
    assert [[n.__dict__ for n in nodes] for nodes in preset.branches.values()] == \
        [[n.__dict__ for n in nodes] for nodes in before.branches.values()]


@pytest.mark.parametrize('module', [job_dir.FACTS_SCRIPT, job_dir.SHOTS_SCRIPT])
def test_a_missing_module_fails_the_discovery(tmp_path, monkeypatch, module):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in (job_dir.FACTS_SCRIPT, job_dir.SHOTS_SCRIPT):
        if name != module:
            shutil.copyfile(job_dir.SCRIPTS_SOURCE / name, scripts / name)
    result, error, _world = run_list(tmp_path, monkeypatch, ue.graph(), copy.deepcopy(SEQ), scripts_dir=str(scripts))
    assert error is not None and result['ok'] is False
    assert result['error'] in ('The preset facts module did not load.', 'The shots module did not load (FileNotFoundError).')
    assert str(tmp_path) not in result['error']
