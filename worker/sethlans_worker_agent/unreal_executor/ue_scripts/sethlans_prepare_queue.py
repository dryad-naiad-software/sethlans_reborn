# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sethlans per-job queue prep (Unreal foundation FR-UE-8, FR-UE-19, FR-UE-25).

Runs in UE 5.8's embedded Python (3.11) as the PythonScript commandlet
(``-run=pythonscript -script=<this file>``); imports only unreal, json, os,
sys and time. Job data comes only from the params file named by
``SETHLANS_UE_JOB_PARAMS``. Modes ``prepare`` (one frame) and
``prepare_pilot`` (one frame per shot). Writes ``result_path`` and, on
failure, re-raises so the commandlet exits with -1.
"""
import json
import os
import sys
import time

import unreal

T0 = time.time()
RES = {"ok": False}
P = {}


def fail(msg):
    raise RuntimeError(msg)


def all_nodes(graph, cls):
    out = []
    for branch in graph.get_branch_names():
        out += [n for n in graph.get_nodes_for_branch(cls, branch, False) if n not in out]
    return out


def bound(value):
    b = unreal.MovieGraphSequencePlaybackRangeBound()
    b.type = unreal.MovieGraphSequenceRangeType.CUSTOM
    b.value = int(value)
    return b


def rate(frame_rate):
    return frame_rate.numerator / frame_rate.denominator


def active(section):
    try:
        return section.is_active()
    except Exception:
        return True


def shot_sections(seq):
    """(start, end, sub, inner_start, offset, inner/outer rate ratio) of every enabled shot section.

    Section bounds are edit display frames; the inner frame of edit frame f is
    ``inner_start + offset + (f - start) * ratio`` (S1 mapping, FR-UE-8 step 5).
    """
    disp = rate(seq.get_display_rate())
    out = []
    for track in seq.get_tracks():
        if not isinstance(track, (unreal.MovieSceneCinematicShotTrack, unreal.MovieSceneSubTrack)):
            continue
        for sec in track.get_sections():
            sub = sec.get_sequence()
            if sub is None or not active(sec):
                continue
            inner_rate = rate(sub.get_display_rate())
            offset = 0.0
            try:
                offset = sec.parameters.start_frame_offset.value * inner_rate / rate(sub.get_tick_resolution())
            except Exception:
                pass
            out.append((sec.get_start_frame(), sec.get_end_frame(), sub, sub.get_playback_start(), offset,
                        inner_rate / disp))
    return out


def cuts_of(seq):
    """Enabled camera-cut sections ``(start, end)`` of a sequence's own camera-cut track."""
    cuts = []
    for track in seq.get_tracks():
        if isinstance(track, unreal.MovieSceneCameraCutTrack):
            cuts += [(s.get_start_frame(), s.get_end_frame()) for s in track.get_sections() if active(s)]
    return cuts


def in_shot_cut(shot, frame):
    start, end, sub, inner_start, offset, ratio = shot
    inner = inner_start + offset + (frame - start) * ratio
    return start <= frame < end and any(cs <= inner < ce for cs, ce in cuts_of(sub))


def cut_starts(seq):
    """Edit frames at which a camera cut begins (own cuts, else via the shots)."""
    cuts = cuts_of(seq)
    if cuts:
        return sorted({s for s, _e in cuts})
    starts = set()
    for start, _end, sub, inner_start, offset, ratio in shot_sections(seq):
        for cs, _ce in cuts_of(sub):
            starts.add(int(round(max(start, start + (cs - inner_start - offset) / ratio))))
    return sorted(starts)


def frame_in_cut(seq, frame):
    cuts = cuts_of(seq)
    if cuts:
        return any(s <= frame < e for s, e in cuts)
    return any(in_shot_cut(shot, frame) for shot in shot_sections(seq))


def pilot_frames(seq, first, last):
    """FR-UE-25: the first qualifying frame of every cut/shot overlapping [first, last)."""
    frames = set()
    cuts = cuts_of(seq)
    if cuts:
        for s, e in cuts:
            if e > first and s < last:
                frames.add(max(s, first))
        return sorted(frames)
    for shot in shot_sections(seq):
        start, end = shot[0], shot[1]
        lo, hi = max(start, first), min(end, last)
        steps = 0
        for f in range(lo, hi):
            steps += 1
            if steps > 1000000:
                break
            if in_shot_cut(shot, f):
                frames.add(f)
                break
    return sorted(frames)


def graph_scalability(dup):
    level = 4  # the MovieGraphGlobalGameOverridesNode default (Cinematic)
    try:
        for node in all_nodes(dup, unreal.MovieGraphGlobalGameOverridesNode):
            if node.get_editor_property("override_scalability_quality_level"):
                level = int(node.get_editor_property("scalability_quality_level").value)
    except Exception as exc:
        RES.setdefault("warnings", []).append(f"scalability not read: {exc}"[:200])
    return level


def override_graph(dup, start, end):
    gos = all_nodes(dup, unreal.MovieGraphGlobalOutputSettingNode)
    if not gos:
        fail("Preset has no Global Output Settings node.")
    for node in gos:
        node.set_editor_property("override_custom_playback_range_start", True)
        node.set_editor_property("custom_playback_range_start", bound(start))
        node.set_editor_property("override_custom_playback_range_end", True)
        node.set_editor_property("custom_playback_range_end", bound(end))
        node.set_editor_property("override_output_directory", True)
        node.set_editor_property("output_directory", unreal.DirectoryPath(path=P["output_dir"]))
    pngs = 0
    for node in all_nodes(dup, unreal.MovieGraphNode):
        if isinstance(node, unreal.MovieGraphImageSequenceOutputNode_PNG):
            node.set_editor_property("override_file_name_format", True)
            node.set_editor_property("file_name_format", P["file_name_format"])
            pngs += 1
        elif isinstance(node, unreal.MovieGraphFileOutputNode) and not node.is_disabled():
            node.set_disabled(True)
    if not pngs:
        fail("Preset does not output PNG; phase 1 does not add outputs.")
    layers = 0
    for branch in dup.get_branch_names():
        rl = [n for n in dup.get_nodes_for_branch(unreal.MovieGraphRenderLayerNode, branch, False) if not n.is_disabled()]
        passes = [n for n in dup.get_nodes_for_branch(unreal.MovieGraphRenderPassNode, branch, False)
                  if not n.is_disabled()]
        if rl:
            layers += len(rl) * max(1, len(passes))  # each enabled layer writes one file per pass
    if layers != 1:
        fail(f"Preset renders {layers} layers; phase 1 supports one.")


def override_legacy(dup, start, end):
    out = dup.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    out.use_custom_playback_range = True
    out.custom_start_frame = int(start)
    out.custom_end_frame = int(end)
    out.output_directory = unreal.DirectoryPath(P["output_dir"])
    out.file_name_format = P["file_name_format"]
    pngs, passes = 0, 0
    for setting in dup.get_all_settings():
        if isinstance(setting, unreal.MoviePipelineImageSequenceOutput_PNG):
            pngs += 1
        elif isinstance(setting, unreal.MoviePipelineOutputBase):
            setting.set_is_enabled(False)
        elif isinstance(setting, unreal.MoviePipelineRenderPass) and setting.is_enabled():
            passes += 1
    if not pngs:
        fail("Preset does not output PNG; phase 1 does not add outputs.")
    if passes > 1:
        fail(f"Preset renders {passes} layers; phase 1 supports one.")


def duplicate(eal, preset_path, kind, index, start, end):
    dup = eal.duplicate_asset(preset_path, f"{P['package_root']}/MRG_Job{P['job_id']}_{index}")
    if dup is None:
        fail("Could not duplicate the preset.")
    if kind == "graph":
        override_graph(dup, start, end)
    else:
        override_legacy(dup, start, end)
    eal.save_loaded_asset(dup, False)
    return dup


def preset_kind(preset):
    if isinstance(preset, unreal.MovieGraphConfig):
        if preset.get_all_contained_subgraphs():
            fail("Preset uses subgraphs; phase 1 does not rewrite subgraph outputs.")
        return "graph"
    if isinstance(preset, unreal.MoviePipelinePrimaryConfig):
        return "legacy"
    fail(f"Preset class {preset.get_class().get_name()} is neither a Movie Render Graph nor an MRQ config.")


def job_frames(seq, first, last):
    if P["mode"] == "prepare_pilot":
        frames = pilot_frames(seq, first, last)
        RES["pilot_frames"] = frames
        if not frames:
            fail(f"No camera cut found in {P['sequence']}; cannot measure.")
        if len(frames) > int(P.get("max_pilot_frames", 256)):
            fail(f"Sequence has {len(frames)} shots; the pilot supports at most {P.get('max_pilot_frames', 256)}.")
        return frames
    frame = int(P["frame"])
    if not first <= frame < last:
        fail(f"Frame {frame} is outside the playback range [{first}, {last}).")
    if not frame_in_cut(seq, frame):
        fail(f"Frame {frame} is not inside a camera cut of {P['sequence']}.")
    return [frame]


def prepare():
    eal = unreal.EditorAssetLibrary
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.search_all_assets(True)
    registry.wait_for_completion()
    root = P["package_root"]
    if eal.does_directory_exist(root) and eal.list_assets(root, True, False):
        fail(f"Package root {root} already exists.")
    preset = unreal.load_asset(P["preset"])
    if preset is None:
        fail(f"Preset {P['preset']} not found.")
    kind = preset_kind(preset)
    RES["preset_kind"] = kind
    seq = unreal.load_asset(P["sequence"])
    if seq is None:
        fail(f"Sequence {P['sequence']} not found.")
    first, last = seq.get_playback_start(), seq.get_playback_end()
    RES["playback_range"] = [first, last]
    RES["camera_cut_starts"] = cut_starts(seq)
    frames = job_frames(seq, first, last)
    queue = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        f"Q_Job{P['job_id']}", root, unreal.MoviePipelineQueue, None)
    for index, frame in enumerate(frames):
        dup = duplicate(eal, P["preset"], kind, index, frame, frame + 1)
        if index == 0:
            RES["scalability"] = graph_scalability(dup) if kind == "graph" else 4
        job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
        job.job_name = f"sethlans_{P['job_id']}_{frame}"
        job.map = unreal.SoftObjectPath(P["map"])
        job.sequence = unreal.SoftObjectPath(P["sequence"])
        if kind == "graph":
            job.set_graph_preset(dup)
        else:
            job.set_preset_origin(dup)
    eal.save_loaded_asset(queue, False)
    RES["queue_jobs"] = len(frames)
    lib = unreal.SystemLibrary
    RES["effective_cvars"] = {name: lib.get_console_variable_string_value(name) for name in P.get("cvar_names", [])}
    RES["queue_asset"] = f"{root}/Q_Job{P['job_id']}"
    RES["ok"] = True


def main():
    global P
    with open(os.environ["SETHLANS_UE_JOB_PARAMS"], encoding="utf-8") as handle:
        P = json.load(handle)
    try:
        prepare()
    except Exception as exc:
        RES["ok"], RES["error"] = False, str(exc)[:2048]
        raise
    finally:
        RES["prep_s"], RES["python"] = round(time.time() - T0, 2), sys.version.split()[0]
        with open(P["result_path"], "w", encoding="utf-8") as handle:
            json.dump(RES, handle, indent=1)
        unreal.log(f"[SethlansPrep] wrote {P['result_path']} ok={RES['ok']}")


main()
