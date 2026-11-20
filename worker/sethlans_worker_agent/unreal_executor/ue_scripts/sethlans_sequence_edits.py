# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Edits on the farm's duplicate of a preset (sequence rendering FR-SW-3, FR-SW-4, D5b, D6, D11).

Runs in UE 5.8's embedded Python, loaded by the prep script from one fixed
file (``importlib.util.spec_from_file_location``, never through
``sys.path``). Data, not a worker module: it imports only ``unreal``.

It edits **only the duplicate** the prep made (and reads the loaded preset
for ``draft_conflicts``); it never saves an asset, so the artist's preset
is never changed. ``apply(dup, kind, params, warnings)``, in this order:

1. **Output format** (D6): PNG keeps the preset's PNG output (none fails,
   as before); EXR uses the preset's EXR output, enabled (graph: exactly
   one node of the exact EXR class, an enabled one preferred, never the
   multilayer subclass; none or several fail; legacy: the setting is added
   when missing). Every other file output is disabled; the kept one writes
   the farm's file name format.
2. **Output settings** (D5b): handle frames 0, frame-number offset 0,
   frame step 1 and zero-pad ``preset's`` clamped to 4-7 on the legacy output
   setting and on every graph Global Output Settings node (with its
   override flag). A property the 5.8.4 class does not have is left (U-1).
3. **Resolution** (D8): ``params["resolution"]`` when set.
4. **Draft** (D11, ``params["draft"]``, pieces only): sample counts are only
   ever lowered (legacy anti-aliasing setting; graph sampling and render
   pass nodes that override them). Motion blur off: in 5.8.4 the shutter
   comes only from each camera's post-process Motion Blur Amount, which no
   MRQ setting overrides and the artist's cameras are never touched, so it
   forces temporal samples to 1 here (no shutter accumulation) and the
   worker sends ``r.MotionBlurQuality=0`` (no post-process blur); ``apply``
   returns that path (``motion_blur_off``). Screen percentage and
   ray-traced effects are CVars (worker side), never set here.

``draft_conflicts(preset, kind, names, warnings)``: the draft CVar names
the preset's own console-variable setting (legacy) or enabled nodes
(graph) also set (D11); unreadable gives a warning and none.

Property names are probed by U-1/U-3 on UE 5.8.4; the sample-count classes
are the ones ``sethlans_preset_facts.py`` reads. Warnings carry fixed text
and a class name only, never an exception's message.
"""
import unreal

PNG_MISSING = "Preset does not output PNG; phase 1 does not add outputs."
EXR_MISSING = "The preset has no EXR output node; add one to render EXR."
EXR_SEVERAL = "The preset has {n} EXR output nodes that could render; keep exactly one enabled to render EXR."
MOTION_BLUR_PATH = "samples1_pp_off"  # temporal samples 1 here, r.MotionBlurQuality=0 from the worker
CVARS_NOT_READ = "The preset's console variables were not read ({cls}); a draft value it sets itself is not detected."
CVAR_PRESETS = "The preset uses console variable presets; a draft value they set is not detected."
NORMALISED = (("handle_frame_count", 0), ("frame_number_offset", 0), ("output_frame_step", 1))
ZERO_PAD, MIN_ZERO_PAD, MAX_ZERO_PAD = "zero_pad_frame_numbers", 4, 7  # the worker reads 4 to 7 digits
SAMPLES = (("temporal_samples", ("MovieGraphSamplingMethodNode",), "temporal_sample_count"),
           ("spatial_samples", ("MovieGraphImagePassBaseNode", "MovieGraphDeferredRenderPassNode"),
            "spatial_sample_count"))
LEGACY_SAMPLES = (("temporal_samples", "temporal_sample_count"), ("spatial_samples", "spatial_sample_count"))
MAX_CONFLICTS = 16


class EditsError(RuntimeError):
    """An edit that cannot be made; the text is fixed: safe to report."""


def _nodes(graph, cls):
    out = []
    for branch in graph.get_branch_names():
        out += [n for n in graph.get_nodes_for_branch(cls, branch, False) if n not in out]
    return out


def _prop(obj, name):
    """The property's value, or ``None`` when the class has no such property."""
    try:
        return obj.get_editor_property(name)
    except Exception:
        return None


def _override(node, name, value):
    node.set_editor_property("override_" + name, True)
    node.set_editor_property(name, value)


def _exr_node(nodes):
    """D6: the one EXR node, by exact class (a multilayer EXR node derives from it and is never used), an enabled
    one preferred; none, or more than one that could render, fails."""
    exact = [n for n in nodes if type(n) is unreal.MovieGraphImageSequenceOutputNode_EXR]
    enabled = [n for n in exact if not n.is_disabled()]
    if not exact:
        raise EditsError(EXR_MISSING)
    if len(enabled) > 1 or (not enabled and len(exact) > 1):
        raise EditsError(EXR_SEVERAL.format(n=len(enabled) or len(exact)))
    return (enabled or exact)[0]


def _graph_outputs(dup, fmt, file_name_format):
    nodes = _nodes(dup, unreal.MovieGraphNode)
    if fmt == "EXR":
        keep = [_exr_node(nodes)]
        keep[0].set_disabled(False)
    else:
        keep = [n for n in nodes if isinstance(n, unreal.MovieGraphImageSequenceOutputNode_PNG)]
    if not keep:
        raise EditsError(PNG_MISSING)
    for node in nodes:
        if any(node is k for k in keep):
            _override(node, "file_name_format", file_name_format)
        elif isinstance(node, unreal.MovieGraphFileOutputNode) and not node.is_disabled():
            node.set_disabled(True)


def _legacy_outputs(dup, fmt):
    wanted = unreal.MoviePipelineImageSequenceOutput_EXR if fmt == "EXR" else unreal.MoviePipelineImageSequenceOutput_PNG
    if fmt == "EXR":  # D6: added when missing; a disabled one is reused and enabled
        dup.find_or_add_setting_by_class(wanted, True).set_is_enabled(True)
    kept = 0
    for setting in dup.get_all_settings():
        if isinstance(setting, wanted):
            kept += 1
        elif isinstance(setting, unreal.MoviePipelineOutputBase):
            setting.set_is_enabled(False)
    if not kept:
        raise EditsError(PNG_MISSING)


def _normalise_legacy(out):
    for name, value in NORMALISED:
        if _prop(out, name) is not None:
            out.set_editor_property(name, value)
    pad = _prop(out, ZERO_PAD)
    if pad is not None:
        out.set_editor_property(ZERO_PAD, min(MAX_ZERO_PAD, max(MIN_ZERO_PAD, int(pad))))


def _normalise_graph(nodes):
    cdo = unreal.get_default_object(unreal.MovieGraphGlobalOutputSettingNode)
    for node in nodes:
        for name, value in NORMALISED:
            if _prop(cdo, name) is not None:  # absent in 5.8.4 (U-1): left
                _override(node, name, value)
        if _prop(cdo, ZERO_PAD) is not None:
            pad = node.get_editor_property(ZERO_PAD) if _prop(node, "override_" + ZERO_PAD) else _prop(cdo, ZERO_PAD)
            _override(node, ZERO_PAD, min(MAX_ZERO_PAD, max(MIN_ZERO_PAD, int(pad))))


def _named_resolution(width, height):
    make = getattr(getattr(unreal, "MovieGraphBlueprintLibrary", None), "named_resolution_from_size", None)
    if make is not None:
        return make(width, height)
    value = unreal.MovieGraphNamedResolution()
    value.set_editor_property("resolution", unreal.IntPoint(width, height))
    return value


def _lower(obj, name, value):
    if _prop(obj, name) is not None:
        obj.set_editor_property(name, min(int(obj.get_editor_property(name)), int(value)))


def _draft_legacy(dup, draft):
    """Sample counts only ever lowered, on the enabled anti-aliasing setting (none: the class defaults, 1 and 1)."""
    aa = dup.find_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)  # enabled only: a disabled one is unused
    for key, name in LEGACY_SAMPLES:
        if aa is not None and key in draft:
            _lower(aa, name, draft[key])


def _draft_graph(dup, draft):
    """Sample counts only ever lowered, on the enabled nodes that override them (others use the default, 1)."""
    for key, class_names, name in SAMPLES:
        classes = [getattr(unreal, c) for c in class_names if hasattr(unreal, c)]
        nodes = [n for cls in classes for n in _nodes(dup, cls)]
        for node in [n for i, n in enumerate(nodes) if n not in nodes[:i]] if key in draft else ():
            if not node.is_disabled() and _prop(node, "override_" + name):
                _lower(node, name, draft[key])


def apply(dup, kind, params, warnings):
    """FR-SW-3 on the duplicate ``dup`` of kind ``graph`` or ``legacy``; ``EditsError`` when it cannot be made.

    Returns the result facts to report: ``motion_blur_off`` (the path a motion-blur draft took) when asked."""
    fmt = params.get("output_format", "PNG")
    size = params.get("resolution")
    draft = dict(params.get("draft") or {})
    blur_off = draft.get("motion_blur") is False
    if blur_off:  # no shutter accumulation; the worker's r.MotionBlurQuality=0 removes the post-process blur
        draft["temporal_samples"] = 1
    if kind == "graph":
        _graph_outputs(dup, fmt, params["file_name_format"])
        nodes = _nodes(dup, unreal.MovieGraphGlobalOutputSettingNode)
        _normalise_graph(nodes)
        for node in nodes if size else ():
            _override(node, "output_resolution", _named_resolution(int(size[0]), int(size[1])))
        _draft_graph(dup, draft)
    else:
        _legacy_outputs(dup, fmt)
        out = dup.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
        _normalise_legacy(out)
        if size:
            out.set_editor_property("output_resolution", unreal.IntPoint(int(size[0]), int(size[1])))
        _draft_legacy(dup, draft)
    return {"motion_blur_off": MOTION_BLUR_PATH} if blur_off else {}


def _field(entry, name, default):
    try:
        return entry.get_editor_property(name)
    except Exception:
        return getattr(entry, name, default)


def _entries(source):
    read = getattr(source, "get_console_variables", None)
    return list(read()) if read is not None else list(source.get_editor_property("cvars"))


def _cvar_sources(preset, kind):
    if kind == "legacy":
        setting = preset.find_setting_by_class(unreal.MoviePipelineConsoleVariableSetting)
        return [setting] if setting is not None else []
    cls = getattr(unreal, "MovieGraphConsoleVariableNode", None)
    return [n for n in _nodes(preset, cls) if not n.is_disabled()] if cls is not None else []


def draft_conflicts(preset, kind, names, warnings):
    """D11: which of ``names`` (the draft CVars sent) the preset sets itself; at most 16."""
    wanted = {str(n).casefold(): str(n) for n in names}
    if not wanted:
        return []
    found = []
    try:
        for source in _cvar_sources(preset, kind):
            if _prop(source, "console_variable_presets"):
                warnings.append(CVAR_PRESETS)
            found += [str(_field(e, "name", "")) for e in _entries(source) if _field(e, "is_enabled", True)]
    except Exception as exc:
        warnings.append(CVARS_NOT_READ.format(cls=type(exc).__name__))
        return []
    hits = [wanted[n.casefold()] for n in found if n.casefold() in wanted]
    return list(dict.fromkeys(hits))[:MAX_CONFLICTS]
