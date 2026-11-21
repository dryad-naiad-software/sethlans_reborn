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
   override flag). UE 5.8.4's graph node has no frame step (U-1), so the
   graph list leaves it out; nothing is probed.
3. **Resolution** (D8): ``params["resolution"]`` when set. Graph: a ``Custom``
   named resolution from ``unreal.MovieGraphLibrary`` (the library's Python
   name), read back; a known profile name would win over the size (#283).
4. **Draft** (D11, ``params["draft"]``, pieces only): sample counts are only
   ever lowered (legacy anti-aliasing setting; graph sampling nodes and
   image pass nodes that override them). Motion blur off: in 5.8.4 the shutter
   comes only from each camera's post-process Motion Blur Amount, which no
   MRQ setting overrides and the artist's cameras are never touched, so it
   forces temporal samples to 1 here (no shutter accumulation) and the
   worker sends ``r.MotionBlurQuality=0`` (no post-process blur); ``apply``
   returns that path (``motion_blur_off``). Screen percentage and
   ray-traced effects are CVars (worker side), never set here.

The helpers ``graph_nodes``, ``prop``, ``override``, ``named_resolution``,
``set_size``, ``normalise_legacy``, ``normalise_graph`` and ``preset_cvars`` are public: the audio edits
module (``sethlans_audio_edits.py``, FR-SA-2) uses them on its duplicate.

``draft_conflicts(preset, kind, names, warnings)``: the draft CVar names
the preset sets itself (D11): legacy, the enabled entries of its console
variable setting; graph, its enabled Set Console Variable nodes (UE 5.8.4
``MovieGraphSetCVarValueNode``, the only graph node that names a CVar).
Console variable presets are not read: a warning says so.

Every Unreal name is UE 5.8.4's Python name (``tools/ue_api``, #283). A
property the class does not have fails with fixed text naming it: there is
no silent default. Errors carry fixed text, a property and a class name only,
never an exception's message.
"""
import unreal

PNG_MISSING = "Preset does not output PNG; phase 1 does not add outputs."
EXR_MISSING = "The preset has no EXR output node; add one to render EXR."
EXR_SEVERAL = "The preset has {n} EXR output nodes that could render; keep exactly one enabled to render EXR."
MOTION_BLUR_PATH = "samples1_pp_off"  # temporal samples 1 here, r.MotionBlurQuality=0 from the worker
CVAR_PRESETS = "The preset uses console variable presets; a draft value they set is not detected."
PROP_MISSING = "Unreal has no property {name} on {cls}; Sethlans needs UE 5.8.4's property names."
NORMALISED = (("handle_frame_count", 0), ("frame_number_offset", 0), ("output_frame_step", 1))
GRAPH_NORMALISED = NORMALISED[:2]  # UE 5.8.4's MovieGraphGlobalOutputSettingNode has no frame step (U-1)
ZERO_PAD, MIN_ZERO_PAD, MAX_ZERO_PAD = "zero_pad_frame_numbers", 4, 7  # the worker reads 4 to 7 digits
SAMPLES = (("temporal_samples", "temporal_sample_count"), ("spatial_samples", "spatial_sample_count"))
MAX_CONFLICTS = 16
SIZE_CALLS = ("named_resolution_from_size", "is_named_resolution_valid")
NO_SIZE_LIBRARY = "Unreal's Movie Graph library cannot make a custom output size; the size cannot be set."
SIZE_NOT_HELD = "The output size {w}x{h} did not hold on the farm's copy of the preset; a preset size would render."


class EditsError(RuntimeError):
    """An edit that cannot be made; the text is fixed: safe to report."""


def graph_nodes(graph, cls):
    out = []
    for branch in graph.get_branch_names():
        out += [n for n in graph.get_nodes_for_branch(cls, branch, False) if n not in out]
    return out


def prop(obj, name):
    """The property's value; one the class does not have fails with fixed text (never a silent default, #283)."""
    try:
        return obj.get_editor_property(name)
    except Exception:
        raise EditsError(PROP_MISSING.format(name=name, cls=type(obj).__name__)) from None


def override(node, name, value):
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
    nodes = graph_nodes(dup, unreal.MovieGraphNode)
    if fmt == "EXR":
        keep = [_exr_node(nodes)]
        keep[0].set_disabled(False)
    else:
        keep = [n for n in nodes if isinstance(n, unreal.MovieGraphImageSequenceOutputNode_PNG)]
    if not keep:
        raise EditsError(PNG_MISSING)
    for node in nodes:
        if any(node is k for k in keep):
            override(node, "file_name_format", file_name_format)
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


def _clamped_pad(pad):
    return min(MAX_ZERO_PAD, max(MIN_ZERO_PAD, int(pad)))


def normalise_legacy(out):
    for name, value in NORMALISED:
        prop(out, name)  # present on UE 5.8.4's MoviePipelineOutputSetting; a missing one fails here
        out.set_editor_property(name, value)
    out.set_editor_property(ZERO_PAD, _clamped_pad(prop(out, ZERO_PAD)))


def normalise_graph(nodes):
    cdo = unreal.get_default_object(unreal.MovieGraphGlobalOutputSettingNode)
    for node in nodes:
        for name, value in GRAPH_NORMALISED:
            override(node, name, value)
        pad = prop(node, ZERO_PAD) if prop(node, "override_" + ZERO_PAD) else prop(cdo, ZERO_PAD)
        override(node, ZERO_PAD, _clamped_pad(pad))


def _size_library():
    """UE's ``UMovieGraphBlueprintLibrary`` by its Python name (``ScriptName="MovieGraphLibrary"``); missing fails."""
    library = getattr(unreal, "MovieGraphLibrary", None)
    if library is None or not all(hasattr(library, name) for name in SIZE_CALLS):
        raise EditsError(NO_SIZE_LIBRARY)
    return library


def named_resolution(width, height):
    """A ``Custom`` named resolution of ``width`` x ``height``. A default-built one names the ``1080p (FHD)``
    profile, and a profile Project Settings knows wins over the stored size at render time (issue #283)."""
    return _size_library().named_resolution_from_size(int(width), int(height))


def set_size(node, width, height):
    """Override a Global Output Settings node's size, then read it back: a stored value that names a known profile
    (which UE renders instead of the stored size) or holds another size fails, so a preset size never wins."""
    library = _size_library()
    override(node, "output_resolution", named_resolution(width, height))
    stored = prop(node, "output_resolution")
    held = stored.resolution
    if library.is_named_resolution_valid(stored.profile_name) or (held.x, held.y) != (width, height):
        raise EditsError(SIZE_NOT_HELD.format(w=int(width), h=int(height)))


def _lower(obj, name, value):
    obj.set_editor_property(name, min(int(prop(obj, name)), int(value)))


def _draft_legacy(dup, draft):
    """Sample counts only ever lowered, on the enabled anti-aliasing setting (none: the class defaults, 1 and 1)."""
    aa = dup.find_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)  # enabled only: a disabled one is unused
    for key, name in SAMPLES:
        if aa is not None and key in draft:
            _lower(aa, name, draft[key])


def _graph_sample_class(key):
    """Temporal samples live on sampling nodes; spatial ones on every image pass node (deferred, path tracer,
    panoramic and object ID all have ``spatial_sample_count`` in UE 5.8.4)."""
    return unreal.MovieGraphSamplingMethodNode if key == "temporal_samples" else unreal.MovieGraphImagePassBaseNode


def _draft_graph(dup, draft):
    """Sample counts only ever lowered, on the enabled nodes that override them (others use the default, 1)."""
    for key, name in SAMPLES:
        for node in graph_nodes(dup, _graph_sample_class(key)) if key in draft else ():
            if not node.is_disabled() and prop(node, "override_" + name):
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
        nodes = graph_nodes(dup, unreal.MovieGraphGlobalOutputSettingNode)
        normalise_graph(nodes)
        for node in nodes if size else ():
            set_size(node, int(size[0]), int(size[1]))
        _draft_graph(dup, draft)
    else:
        _legacy_outputs(dup, fmt)
        out = dup.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
        normalise_legacy(out)
        if size:
            out.set_editor_property("output_resolution", unreal.IntPoint(int(size[0]), int(size[1])))
        _draft_legacy(dup, draft)
    return {"motion_blur_off": MOTION_BLUR_PATH} if blur_off else {}


def _legacy_cvars(preset):
    setting = preset.find_setting_by_class(unreal.MoviePipelineConsoleVariableSetting)
    if setting is None:
        return [], False
    names = [str(prop(e, "name")) for e in setting.get_console_variables() if prop(e, "is_enabled")]
    return names, bool(prop(setting, "console_variable_presets"))


def _graph_cvars(preset):
    """The CVar names of the enabled Set Console Variable nodes that set one (``MovieGraphCVarManager.cpp``)."""
    presets = [n for n in graph_nodes(preset, unreal.MovieGraphApplyCVarPresetNode) if not n.is_disabled()]
    nodes = [n for n in graph_nodes(preset, unreal.MovieGraphSetCVarValueNode) if not n.is_disabled()]
    names = [str(prop(n, "name")) for n in nodes if prop(n, "override_name")]
    return names, any(prop(n, "override_console_variable_preset") for n in presets)


def preset_cvars(preset, kind):
    """``(names, uses_presets)``: the CVars the preset sets itself, and whether console variable presets (whose
    values are not read) apply too. Public: the audio edits refuse presets that set the sound CVars."""
    return (_legacy_cvars if kind == "legacy" else _graph_cvars)(preset)


def draft_conflicts(preset, kind, names, warnings):
    """D11: which of ``names`` (the draft CVars sent) the preset sets itself; at most 16."""
    wanted = {str(n).casefold(): str(n) for n in names}
    if not wanted:
        return []
    found, uses_presets = preset_cvars(preset, kind)
    if uses_presets:
        warnings.append(CVAR_PRESETS)
    hits = [wanted[n.casefold()] for n in found if n.casefold() in wanted]
    return list(dict.fromkeys(hits))[:MAX_CONFLICTS]
