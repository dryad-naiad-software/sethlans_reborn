# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sethlans per-job queue prep (Unreal foundation FR-UE-8, FR-UE-19, FR-UE-25; GPU memory reader FR-GC-3;
sequence rendering FR-SW-4).

Runs in UE 5.8's embedded Python (3.11) as the PythonScript commandlet (``-run=pythonscript -script=<this file>``);
imports only unreal, importlib, json, os, sys and time, and loads ``sethlans_shots.py`` (required) and
``sethlans_preset_facts.py`` from ``scripts_dir`` by file path, never through ``sys.path``. Job data comes only
from the params file named by ``SETHLANS_UE_JOB_PARAMS``. Modes ``prepare`` (one frame) and ``prepare_pilot``
(one frame per shot). Writes ``result_path`` and, on failure, re-raises so the commandlet exits with -1.
"""
import importlib.util
import json
import os
import sys
import time

import unreal

T0 = time.time()
RES = {"ok": False, "warnings": []}
P = {}
FACTS = "sethlans_preset_facts"
SHOTS = "sethlans_shots"


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


def load_shots():
    """FR-SW-4: the shot and cut helpers; required, so a module that does not load fails the prep (class name only)."""
    try:  # one fixed file; nothing is added to sys.path, so nothing else can be imported in its place
        spec = importlib.util.spec_from_file_location(SHOTS, os.path.join(P["scripts_dir"], SHOTS + ".py"))
        shots = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shots)
    except Exception as exc:
        fail(f"The shots module did not load ({type(exc).__name__}).")  # never the message: it can carry a path
    return shots


def read_facts(preset, kind):
    """FR-GC-1, FR-GC-3: what the loaded preset renders with; scalability 4 and ``unknown`` when unreadable."""
    RES["scalability"], RES["game_overrides"], RES["texture_streaming"] = 4, "unknown", "unknown"
    try:  # one fixed file; nothing is added to sys.path, so nothing else can be imported in its place
        spec = importlib.util.spec_from_file_location(FACTS, os.path.join(P["scripts_dir"], FACTS + ".py"))
        facts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(facts)
    except Exception as exc:
        RES["warnings"].append(f"The preset facts module did not load ({type(exc).__name__}).")  # never the message
        return
    RES["game_overrides"], RES["texture_streaming"] = facts.game_overrides(preset, kind, RES["warnings"])
    if kind == "graph":  # the duplicates keep the preset's Game Overrides nodes unchanged
        RES["scalability"] = facts.graph_scalability(preset, RES["warnings"])


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
    (override_graph if kind == "graph" else override_legacy)(dup, start, end)
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


def job_frames(shots, seq, first, last):
    if P["mode"] == "prepare_pilot":
        frames = shots.pilot_frames(seq, first, last)
        RES["pilot_frames"] = frames
        if not frames:
            fail(f"No camera cut found in {P['sequence']}; cannot measure.")
        if len(frames) > int(P.get("max_pilot_frames", 256)):
            fail(f"Sequence has {len(frames)} shots; the pilot supports at most {P.get('max_pilot_frames', 256)}.")
        return frames
    frame = int(P["frame"])
    if not first <= frame < last:
        fail(f"Frame {frame} is outside the playback range [{first}, {last}).")
    if not shots.frame_in_cut(seq, frame):
        fail(f"Frame {frame} is not inside a camera cut of {P['sequence']}.")
    return [frame]


def prepare():
    shots = load_shots()
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
    read_facts(preset, kind)
    seq = unreal.load_asset(P["sequence"])
    if seq is None:
        fail(f"Sequence {P['sequence']} not found.")
    first, last = seq.get_playback_start(), seq.get_playback_end()
    RES["playback_range"] = [first, last]
    RES["camera_cut_starts"] = shots.cut_starts(seq)
    frames = job_frames(shots, seq, first, last)
    queue = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        f"Q_Job{P['job_id']}", root, unreal.MoviePipelineQueue, None)
    for index, frame in enumerate(frames):
        dup = duplicate(eal, P["preset"], kind, index, frame, frame + 1)
        job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
        job.job_name = f"sethlans_{P['job_id']}_{frame}"
        job.map = unreal.SoftObjectPath(P["map"])
        job.sequence = unreal.SoftObjectPath(P["sequence"])
        (job.set_graph_preset if kind == "graph" else job.set_preset_origin)(dup)
    eal.save_loaded_asset(queue, False)
    RES["queue_jobs"] = len(frames)
    RES["effective_cvars"] = {n: unreal.SystemLibrary.get_console_variable_string_value(n) for n in P.get("cvar_names", [])}
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
