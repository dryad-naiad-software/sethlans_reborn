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

Warnings carry fixed text and the exception's class name only, never its
message (it can name local or asset paths and is sent to the manager).

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
