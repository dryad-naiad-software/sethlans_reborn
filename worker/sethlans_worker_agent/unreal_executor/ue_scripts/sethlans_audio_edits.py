# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Edits on an audio job's duplicate of a preset (sequence rendering FR-SA-2, D5b, D7, U-4).

Runs in UE 5.8's embedded Python, loaded by the prep script from one fixed
file (``importlib.util.spec_from_file_location``, never through
``sys.path``) in mode ``prepare_audio``. Data, not a worker module: it
imports only ``unreal``, and uses the edits module's public helpers
(``sethlans_sequence_edits.py``, handed over by ``using(edits)``).

It edits **only the duplicate** the prep made; it never saves an asset, so
the artist's preset is never changed. ``apply(dup, kind, params, warnings,
edits)``, the research's audio-only recipe:

1. **Outputs** (D7): every image and video output is disabled and the sound
   output kept, writing the farm's file name format. Legacy: the wave output
   is added when missing (enabled), its own file name override cleared so the
   output setting's farm format applies. Graph: the preset's one audio output
   node (an enabled one preferred) is enabled; none, or more than one that
   could render, fails with fixed text.
2. **Output settings** (D5b), as on every farm duplicate.
3. **Size**: 128x72; the render pass is kept (MRG writes no ``.wav``
   without image data reaching its outputs).
4. **Temporal samples 2** (1 drifts about a frame per shot): legacy on the
   enabled anti-aliasing setting (added with the class defaults when
   missing); graph on every enabled sampling-method node (none fails).

Everything else stays as the preset has it: engine warm-up, motion-blur
emulation, spatial samples. Property names are U-4's to confirm on 5.8.4.
"""
import unreal

WIDTH, HEIGHT, TEMPORAL = 128, 72, 2
NO_AUDIO = "The preset has no audio output node; add one to render sound."
AUDIO_SEVERAL = "The preset has {n} audio output nodes that could render; keep exactly one enabled to render sound."
NO_SAMPLING = "The preset has no enabled sampling node to set 2 temporal samples; add one to render sound."


class AudioEditsError(RuntimeError):
    """An edit that cannot be made; the text is fixed: safe to report."""


def _audio_node(nodes):
    """D7: the preset's one audio output node, an enabled one preferred; none, or several that could render, fail."""
    found = [n for n in nodes if isinstance(n, unreal.MovieGraphAudioOutputNode)]
    enabled = [n for n in found if not n.is_disabled()]
    if not found:
        raise AudioEditsError(NO_AUDIO)
    if len(enabled) > 1 or (not enabled and len(found) > 1):
        raise AudioEditsError(AUDIO_SEVERAL.format(n=len(enabled) or len(found)))
    return (enabled or found)[0]


def _graph(dup, params, edits):
    nodes = edits.graph_nodes(dup, unreal.MovieGraphNode)
    keep = _audio_node(nodes)
    keep.set_disabled(False)
    edits.override(keep, "file_name_format", params["file_name_format"])
    for node in nodes:
        if node is not keep and isinstance(node, unreal.MovieGraphFileOutputNode) and not node.is_disabled():
            node.set_disabled(True)
    globals_ = edits.graph_nodes(dup, unreal.MovieGraphGlobalOutputSettingNode)
    edits.normalise_graph(globals_)
    for node in globals_:
        edits.override(node, "output_resolution", edits.named_resolution(WIDTH, HEIGHT))
    sampling = [n for n in edits.graph_nodes(dup, unreal.MovieGraphSamplingMethodNode) if not n.is_disabled()]
    if not sampling:
        raise AudioEditsError(NO_SAMPLING)
    for node in sampling:
        edits.override(node, "temporal_sample_count", TEMPORAL)


def _legacy(dup, edits):
    wave = dup.find_or_add_setting_by_class(unreal.MoviePipelineWaveOutput, True)
    wave.set_is_enabled(True)
    if edits.prop(wave, "file_name_format_override") is not None:
        wave.set_editor_property("file_name_format_override", "")
    for setting in dup.get_all_settings():
        if isinstance(setting, unreal.MoviePipelineOutputBase) and not isinstance(setting, unreal.MoviePipelineWaveOutput):
            setting.set_is_enabled(False)
    out = dup.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    edits.normalise_legacy(out)
    out.set_editor_property("output_resolution", unreal.IntPoint(WIDTH, HEIGHT))
    aa = dup.find_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)  # enabled only: a disabled one is unused
    if aa is None:
        aa = dup.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting, True)
        aa.set_is_enabled(True)
    aa.set_editor_property("temporal_sample_count", TEMPORAL)


def apply(dup, kind, params, warnings, edits):
    """FR-SA-2 on the duplicate ``dup`` of kind ``graph`` or ``legacy``; ``AudioEditsError`` when it cannot be made."""
    if kind == "graph":
        _graph(dup, params, edits)
    else:
        _legacy(dup, edits)
    return {}


def using(edits):
    """``apply`` as the prep calls every duplicate edit, with the edits module's helpers bound in."""
    def edit(dup, kind, params, warnings):
        return apply(dup, kind, params, warnings, edits)
    return edit
