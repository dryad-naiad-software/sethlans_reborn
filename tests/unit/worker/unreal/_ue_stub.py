# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A stub of UE 5.8's ``unreal`` Python module, just enough to run the worker's
UE scripts (``ue_scripts/``) outside Unreal: Movie Render Graph presets,
level sequences with camera-cut and shot tracks, the asset library and the
queue. ``make_unreal(world)`` returns the module; ``world`` holds the assets
and records what the script did.
"""
import copy
import types


class FrameRate:
    def __init__(self, numerator, denominator=1):
        self.numerator, self.denominator = numerator, denominator


class _Props:
    def __init__(self, **props):
        self.props = dict(props)
        self.disabled = props.pop('disabled', False)

    def set_editor_property(self, name, value):
        self.props[name] = value

    def get_editor_property(self, name):
        return self.props.get(name)

    def is_disabled(self):
        return self.disabled

    def set_disabled(self, value):
        self.disabled = value


class MovieGraphNode(_Props):
    pass


class MovieGraphGlobalOutputSettingNode(MovieGraphNode):
    pass


class MovieGraphGlobalGameOverridesNode(MovieGraphNode):
    pass


class MovieGraphFileOutputNode(MovieGraphNode):
    pass


class MovieGraphImageSequenceOutputNode_PNG(MovieGraphFileOutputNode):
    pass


class MovieGraphImageSequenceOutputNode_EXR(MovieGraphFileOutputNode):
    pass


class MovieGraphRenderLayerNode(MovieGraphNode):
    pass


class MovieGraphRenderPassNode(MovieGraphNode):
    pass


class MovieGraphConfig:
    def __init__(self, branches, subgraphs=()):
        self.branches, self.subgraphs = branches, list(subgraphs)

    def get_branch_names(self):
        return list(self.branches)

    def get_nodes_for_branch(self, cls, branch, recursive):
        return [node for node in self.branches[branch] if isinstance(node, cls)]

    def get_all_contained_subgraphs(self):
        return self.subgraphs

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: 'MovieGraphConfig')


class MoviePipelinePrimaryConfig:
    pass


class Section:
    def __init__(self, start, end, sequence=None, offset_ticks=0, active=True):
        self.start, self.end, self.sequence, self.active = start, end, sequence, active
        self.parameters = types.SimpleNamespace(start_frame_offset=types.SimpleNamespace(value=offset_ticks))

    def get_start_frame(self):
        return self.start

    def get_end_frame(self):
        return self.end

    def is_active(self):
        return self.active

    def get_sequence(self):
        return self.sequence


class Track:
    def __init__(self, sections):
        self.sections = sections

    def get_sections(self):
        return self.sections


class MovieSceneCameraCutTrack(Track):
    pass


class MovieSceneCinematicShotTrack(Track):
    pass


class MovieSceneSubTrack(Track):
    pass


class Sequence:
    def __init__(self, start, end, tracks=(), rate=24, ticks=24000, name='Seq'):
        self.start, self.end, self.tracks, self.rate, self.ticks, self.name = start, end, list(tracks), rate, ticks, name

    def get_display_rate(self):
        return FrameRate(self.rate)

    def get_tick_resolution(self):
        return FrameRate(self.ticks)

    def get_playback_start(self):
        return self.start

    def get_playback_end(self):
        return self.end

    def get_tracks(self):
        return self.tracks

    def get_name(self):
        return self.name


class Queue:
    def __init__(self):
        self.jobs = []

    def allocate_new_job(self, cls):
        job = types.SimpleNamespace(graph=None, origin=None)
        job.set_graph_preset = lambda preset: setattr(job, 'graph', preset)
        job.set_preset_origin = lambda preset: setattr(job, 'origin', preset)
        self.jobs.append(job)
        return job


def make_unreal(world):
    """``world``: {'assets': {path: object}, 'cvars': {name: value}, 'existing_root': bool}."""
    world.setdefault('saved', [])
    world.setdefault('logs', [])
    world.setdefault('queues', [])
    module = types.ModuleType('unreal')
    for cls in (FrameRate, MovieGraphNode, MovieGraphGlobalOutputSettingNode, MovieGraphGlobalGameOverridesNode,
                MovieGraphFileOutputNode, MovieGraphImageSequenceOutputNode_PNG, MovieGraphRenderLayerNode,
                MovieGraphRenderPassNode, MovieGraphConfig, MoviePipelinePrimaryConfig, MovieSceneCameraCutTrack,
                MovieSceneCinematicShotTrack, MovieSceneSubTrack):
        setattr(module, cls.__name__, cls)
    module.log = world['logs'].append
    module.MovieGraphSequenceRangeType = types.SimpleNamespace(CUSTOM='custom')
    module.MovieGraphSequencePlaybackRangeBound = lambda: types.SimpleNamespace(type=None, value=None)
    module.DirectoryPath = lambda path: types.SimpleNamespace(path=path)
    module.SoftObjectPath = lambda path: path
    module.MoviePipelineQueue = Queue
    module.MoviePipelineExecutorJob = object
    module.load_asset = lambda path: world['assets'].get(path)
    registry = types.SimpleNamespace(search_all_assets=lambda sync: world.__setitem__('searched', sync),
                                     wait_for_completion=lambda: None)
    module.AssetRegistryHelpers = types.SimpleNamespace(get_asset_registry=lambda: registry)

    def duplicate(src, dst):
        dup = copy.deepcopy(world['assets'][src])
        world['assets'][dst] = dup
        return dup

    def create_asset(name, root, cls, factory):
        queue = cls()
        world['queues'].append((f"{root}/{name}", queue))
        return queue
    module.EditorAssetLibrary = types.SimpleNamespace(
        does_directory_exist=lambda root: world.get('existing_root', False),
        list_assets=lambda root, recursive, folders: ['x'] if world.get('existing_root') else [],
        duplicate_asset=duplicate, save_loaded_asset=lambda asset, only_dirty: world['saved'].append(asset))
    module.AssetToolsHelpers = types.SimpleNamespace(
        get_asset_tools=lambda: types.SimpleNamespace(create_asset=create_asset))
    module.SystemLibrary = types.SimpleNamespace(
        get_console_variable_string_value=lambda name: world.get('cvars', {}).get(name, ''))
    return module


def graph(layers=1, passes=1, png=True, extra_outputs=(), subgraphs=(), scalability=None):
    nodes = {'Globals': [MovieGraphGlobalOutputSettingNode()], 'DefaultLayer': []}
    if scalability is not None:
        nodes['Globals'].append(MovieGraphGlobalGameOverridesNode(
            override_scalability_quality_level=True,
            scalability_quality_level=types.SimpleNamespace(value=scalability)))
    for index in range(layers):
        nodes['DefaultLayer'].append(MovieGraphRenderLayerNode(layer_name=f'l{index}'))
    for _ in range(passes):
        nodes['DefaultLayer'].append(MovieGraphRenderPassNode())
    if png:
        nodes['DefaultLayer'].append(MovieGraphImageSequenceOutputNode_PNG())
    nodes['DefaultLayer'].extend(extra_outputs)
    return MovieGraphConfig(nodes, subgraphs)


def shot(start, end, inner_cuts, inner_start=0, offset_frames=0):
    """A shot section whose sub-sequence has its own camera cuts (24 fps, 24,000 ticks)."""
    sub = Sequence(inner_start, inner_start + 10000, [MovieSceneCameraCutTrack([Section(s, e) for s, e in inner_cuts])])
    return Section(start, end, sequence=sub, offset_ticks=offset_frames * 1000)
