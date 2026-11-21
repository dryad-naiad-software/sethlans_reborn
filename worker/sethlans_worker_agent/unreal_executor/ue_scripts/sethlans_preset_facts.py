# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a render runs with, read from the job's preset (GPU memory reader D23, FR-GC-1, FR-GC-2).

Runs in UE 5.8's embedded Python, loaded by the prep script from one fixed
file (``importlib.util.spec_from_file_location``, never through
``sys.path``). Data, not a worker module: it imports only ``unreal``.

It **only reads** the preset: it never sets a property, adds a setting,
enables or disables anything, or saves an asset (D23).

* ``graph_scalability(dup, warnings)``: the Movie Render Graph scalability
  level the warm-up uses.
* ``game_overrides(preset, kind, warnings)``: ``(game_overrides,
  texture_streaming)`` with the FR-GC-2 words.

Graph rule (both functions): disabled ``MovieGraphGlobalGameOverridesNode``s
are skipped; no enabled node gives the node's class defaults (scalability 4,
streaming not disabled); enabled nodes that agree give their values; enabled
nodes that set a value differently give scalability 4, ``unknown`` and one
warning. A node that does not override a value does not take part for it
(the graph takes the class default for an un-overridden value). An enabled
``MovieGraphSubgraphNode`` hides nodes this module does not read, so it gets
the conflict rule too (scalability 4, ``unknown``, one warning).

* ``output_facts(preset, kind)``: shot discovery's output facts (sequence
  rendering FR-SD-5, D5b, D6, D7): resolution, sample counts, which outputs
  the farm can use, and the output settings the farm's duplicate normalises.

Warnings carry fixed text and the exception's class name only, never its
message (it can name local or asset paths and is sent to the manager).

Output settings (U-1): legacy ``MoviePipelineOutputSetting`` (the class
default object when the preset has none); graph: the enabled Global Output
Settings nodes that override a value give it, else the node class default.
The graph node's frame step, which UE 5.8.4's class does not have (U-1),
is reported as ``None`` (absent); any other missing property fails with
fixed text naming it (#283: no silent default). A graph's resolution is the
size UE renders: a profile name Project Settings knows wins over the stored
size (``unreal.MovieGraphLibrary``).
A custom frame rate counts only when it is switched on (legacy
``use_custom_frame_rate``, graph ``override_output_frame_rate``). A graph
whose enabled node exposes one of these values as a pin or variable fails
discovery (the stored value is not what renders).

Legacy rule (UE 5.8.4 ``MoviePipelinePrimaryConfig.cpp:33-60, 217-250``,
``MoviePipelineConfigBase.h:96-115``): ``find_setting_by_class`` skips a
disabled setting unless asked, so this module asks for disabled ones too.
With no Game Overrides setting at all, every executor adds a transient one
with the class defaults (``TextureStreaming = Disabled``); a present but
disabled setting stops that transient one and applies nothing itself.
"""
import unreal

DEFAULT_SCALABILITY = 4  # EMovieGraphScalabilityQualityLevel::Cinematic, the node's class default
CONFLICT = "The preset has Game Overrides nodes that disagree; the render's settings are unknown."
SUBGRAPH = "The preset uses a subgraph; its Game Overrides are not read, so the render's settings are unknown."
UNKNOWN = ("unknown", "unknown")
_LEGACY_STREAMING = (("DISABLED", "off"), ("FULLY_LOAD", "fully_load"), ("NONE", "unchanged"))
OUTPUT_FAILED = "The preset's output settings were not read ({cls})."
OUTPUT_CONFLICT = "The preset's Global Output Settings nodes disagree on {name}; the render's output is unknown."
# (fact, property) pairs read as ints from the legacy output setting and the graph's Global Output Settings node.
_OUTPUT_INTS = (("frame_step", "output_frame_step"), ("handle_frames", "handle_frame_count"),
                ("frame_number_offset", "frame_number_offset"), ("zero_pad", "zero_pad_frame_numbers"))


EXPOSED = ("The preset's Global Output Settings expose {name} as a pin or variable; Sethlans cannot read the value "
           "that renders.")
_D5B_PROPS = ("output_resolution", "output_frame_rate") + tuple(name for _key, name in _OUTPUT_INTS)
_GRAPH_INTS = _OUTPUT_INTS[1:]  # UE 5.8.4's MovieGraphGlobalOutputSettingNode has no frame step (U-1): absent
PROP_MISSING = "Unreal has no property {name} on {cls}; the preset's output settings were not read."
ADAPT_WARNING = ("The preset adapts its output resolution to the camera; a shot whose camera constrains its aspect "
                 "ratio renders at another size than the one set, which Sethlans then refuses.")


class _Conflict(Exception):
    pass


class FactsError(RuntimeError):
    """``output_facts`` could not report the facts; the text is fixed (and a class name): safe to report."""


def _warn(warnings, text):
    text = str(text)[:200]
    if text not in warnings:
        warnings.append(text)


def _all_nodes(graph, cls):
    out = []
    for branch in graph.get_branch_names():
        out += [n for n in graph.get_nodes_for_branch(cls, branch, False) if n not in out]
    return out


def _overridden(nodes, flag, name, convert):
    """The set of values the enabled nodes that override ``name`` give it."""
    return {convert(node.get_editor_property(name)) for node in nodes if node.get_editor_property(flag)}


def _graph_facts(graph, warnings):
    """``(scalability, game_overrides, texture_streaming)`` of a Movie Render Graph."""
    if any(not n.is_disabled() for n in _all_nodes(graph, unreal.MovieGraphSubgraphNode)):
        _warn(warnings, SUBGRAPH)
        return (DEFAULT_SCALABILITY,) + UNKNOWN
    nodes = [n for n in _all_nodes(graph, unreal.MovieGraphGlobalGameOverridesNode) if not n.is_disabled()]
    if not nodes:
        return DEFAULT_SCALABILITY, "none", "unchanged"
    levels = _overridden(nodes, "override_scalability_quality_level", "scalability_quality_level",
                         lambda level: int(level.value))
    streaming = _overridden(nodes, "override_disable_texture_streaming", "disable_texture_streaming", bool)
    if len(levels) > 1 or len(streaming) > 1:
        _warn(warnings, CONFLICT)
        return (DEFAULT_SCALABILITY,) + UNKNOWN
    level = levels.pop() if levels else DEFAULT_SCALABILITY
    return level, "preset", "off" if streaming == {True} else "unchanged"


def _legacy_facts(preset):
    setting = preset.find_setting_by_class(unreal.MoviePipelineGameOverrideSetting, True)  # disabled ones too
    if setting is None:
        return "implicit", "off"  # MRQ adds its own with the class defaults: streaming disabled
    if not setting.is_enabled():
        return "none", "unchanged"  # it blocks the implicit one and applies nothing
    mode = setting.get_editor_property("texture_streaming")
    methods = unreal.MoviePipelineTextureStreamingMethod
    for name, word in _LEGACY_STREAMING:
        if mode == getattr(methods, name):
            return "preset", word
    return "preset", "unknown"


def graph_scalability(dup, warnings):
    """The scalability level of a Movie Render Graph preset (4 when unreadable or conflicting)."""
    try:
        return _graph_facts(dup, warnings)[0]
    except Exception as exc:
        _warn(warnings, f"The preset's scalability was not read ({type(exc).__name__}); Cinematic is used.")
        return DEFAULT_SCALABILITY


def game_overrides(preset, kind, warnings):
    """``(game_overrides, texture_streaming)`` of the loaded preset; ``unknown`` when unreadable."""
    try:
        if kind == "graph":
            return _graph_facts(preset, warnings)[1:]
        if kind == "legacy":
            return _legacy_facts(preset)
    except Exception as exc:
        _warn(warnings, f"The preset's Game Overrides were not read ({type(exc).__name__}).")
    return UNKNOWN


def _prop(obj, name):
    """The property's value; one the class does not have fails with fixed text (never a silent default, #283)."""
    try:
        return obj.get_editor_property(name)
    except Exception:
        raise FactsError(PROP_MISSING.format(name=name, cls=type(obj).__name__)) from None


def _rate(value):
    return [int(value.numerator), int(value.denominator)]


def _size(point):
    return [int(point.x), int(point.y)]


def _rendered_size(named):
    """The size UE 5.8.4 renders a graph's named resolution at (``GetDesiredOutputResolution``): a profile name
    Project Settings knows gives that profile's size, whatever size is stored; any other name the stored size."""
    library = unreal.MovieGraphLibrary  # UMovieGraphBlueprintLibrary's Python name (ScriptName)
    if library.is_named_resolution_valid(named.profile_name):
        named = library.named_resolution_from_profile(named.profile_name)
    return _size(named.resolution)


def _int(value):
    return None if value is None else int(value)


def _legacy_output(preset):
    out = preset.find_setting_by_class(unreal.MoviePipelineOutputSetting)
    if out is None:  # MRQ then uses the class defaults
        out = unreal.get_default_object(unreal.MoviePipelineOutputSetting)
    custom = _rate(_prop(out, "output_frame_rate")) if _prop(out, "use_custom_frame_rate") else None
    facts = {"resolution": _size(_prop(out, "output_resolution")), "custom_frame_rate": custom}
    facts.update((key, _int(_prop(out, name))) for key, name in _OUTPUT_INTS)
    return facts


def _overriding(nodes, name, convert):
    """The values of ``name`` on the enabled nodes that override it; nodes that disagree raise."""
    values = [convert(_prop(node, name)) for node in nodes if _prop(node, "override_" + name)]
    if any(value != values[0] for value in values):
        raise _Conflict(OUTPUT_CONFLICT.format(name=name))
    return values


def _graph_value(nodes, cdo, name, convert):
    values = _overriding(nodes, name, convert)
    return values[0] if values else convert(_prop(cdo, name))


def _flat(name):
    return str(name).replace("_", "").lower()  # "ZeroPadFrameNumbers" (a pin's property name) == zero_pad_frame_numbers


def _refuse_exposed(nodes):
    """A D5b value promoted to a pin or variable renders with what feeds it, not the stored value: refused."""
    wanted = {_flat(name): name for name in _D5B_PROPS}
    for node in nodes:
        for info in node.get_exposed_properties():
            if _flat(info.name) in wanted:
                raise _Conflict(EXPOSED.format(name=wanted[_flat(info.name)]))


def _graph_output(graph):
    cls = unreal.MovieGraphGlobalOutputSettingNode
    nodes = [node for node in _all_nodes(graph, cls) if not node.is_disabled()]
    _refuse_exposed(nodes)
    cdo = unreal.get_default_object(cls)
    rates = _overriding(nodes, "output_frame_rate", _rate)
    facts = {"resolution": _graph_value(nodes, cdo, "output_resolution", _rendered_size),
             "custom_frame_rate": rates[0] if rates else None, "frame_step": None}  # absent: the duplicate leaves it
    facts.update((key, _graph_value(nodes, cdo, name, int)) for key, name in _GRAPH_INTS)
    return facts


def _max_override(graph, cls, name):
    """The largest overridden ``name`` on the enabled nodes of ``cls``; 1 when none sets it."""
    values = [int(_prop(node, name)) for node in _all_nodes(graph, cls)
              if not node.is_disabled() and _prop(node, "override_" + name)]
    return max(values, default=1)


def _samples(preset, kind):
    if kind == "legacy":
        aa = preset.find_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
        if aa is None:
            return 1, 1
        # Bound first: returned tuples hold no strings (a contract test reads their strings as the module's words).
        temporal, spatial = _prop(aa, "temporal_sample_count"), _prop(aa, "spatial_sample_count")
        return int(temporal), int(spatial)
    temporal = _max_override(preset, unreal.MovieGraphSamplingMethodNode, "temporal_sample_count")
    spatial = _max_override(preset, unreal.MovieGraphImagePassBaseNode, "spatial_sample_count")  # every image pass
    return temporal, spatial


def _outputs(preset, kind):
    """What the farm can render to (D6, D7): legacy duplicates get EXR and wave outputs added; graphs need the nodes."""
    if kind == "legacy":
        png = any(isinstance(s, unreal.MoviePipelineImageSequenceOutput_PNG) for s in preset.get_all_settings())
        return {"can_png": png, "can_exr": True, "can_sound": True}
    nodes = _all_nodes(preset, unreal.MovieGraphNode)
    return {"can_png": any(isinstance(n, unreal.MovieGraphImageSequenceOutputNode_PNG) for n in nodes),
            "can_exr": any(type(n) is unreal.MovieGraphImageSequenceOutputNode_EXR for n in nodes),  # not multilayer
            "can_sound": any(isinstance(n, unreal.MovieGraphAudioOutputNode) for n in nodes)}


def output_warnings(preset, kind):
    """D8: a graph whose Global Output Settings adapt the resolution (``adapt_resolution`` not Disabled) renders a
    constrained camera's aspect ratio (``GetDesiredOutputResolution``, MovieGraphBlueprintLibrary.cpp:653-701). The
    cameras are not read here (the prep loads no level), so it is a warning whenever adapting is on."""
    if kind != "graph":
        return []
    cls = unreal.MovieGraphGlobalOutputSettingNode
    nodes = [node for node in _all_nodes(preset, cls) if not node.is_disabled()]
    values = [_prop(node, "adapt_resolution") for node in nodes if _prop(node, "override_adapt_resolution")]
    values = values or [_prop(unreal.get_default_object(cls), "adapt_resolution")]
    disabled = unreal.MovieGraphAspectRatioAdaptBehavior.DISABLED
    return [ADAPT_WARNING] if any(value != disabled for value in values) else []


def output_facts(preset, kind):
    """FR-SD-5, D5b: the preset's output facts; ``FactsError`` with fixed text (and a class name) when unreadable."""
    try:
        facts = (_legacy_output if kind == "legacy" else _graph_output)(preset)
        facts["temporal_samples"], facts["spatial_samples"] = _samples(preset, kind)
        facts.update(_outputs(preset, kind))
    except (_Conflict, FactsError) as exc:
        raise FactsError(str(exc)) from None
    except AttributeError as exc:  # a missing Unreal name: only the name, never the message (#283)
        name = exc.name if isinstance(exc.name, str) and exc.name.isidentifier() else None
        raise FactsError(OUTPUT_FAILED.format(cls=f"AttributeError: no {name}" if name else "AttributeError")) from None
    except Exception as exc:
        raise FactsError(OUTPUT_FAILED.format(cls=type(exc).__name__)) from None
    return facts
