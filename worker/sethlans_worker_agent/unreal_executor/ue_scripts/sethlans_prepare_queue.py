# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sethlans per-job queue prep (Unreal foundation FR-UE-8, FR-UE-19, FR-UE-25; GPU memory reader FR-GC-3;
sequence rendering FR-SW-4).

Runs in UE 5.8's embedded Python (3.11) as the PythonScript commandlet (``-run=pythonscript -script=<this file>``);
imports only unreal, importlib, json, os, sys and time; loads ``sethlans_shots.py``, ``sethlans_sequence_edits.py``
(both required), ``sethlans_audio_edits.py`` (mode ``prepare_audio``, required there) and ``sethlans_preset_facts.py``
from ``scripts_dir`` by file path, never through ``sys.path``.
Job data comes only from the params file named by ``SETHLANS_UE_JOB_PARAMS``. Modes ``prepare`` (one frame),
``prepare_pilot`` (one frame per shot, or the manager's ``pilot_frames``), ``prepare_range`` (a piece: one
duplicate over ``range``, FR-SW-3; every duplicate gets the edits module's changes), ``prepare_audio`` (an audio
job: the same over ``range`` with the audio edits instead, FR-SA-2) and ``list_shots`` (FR-SD-4, FR-SD-5: shots,
rate, preset facts; no queue asset, nothing saved). Every result has ``playback_range`` and ``frame_rate``. Writes
``result_path``; on failure re-raises.
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
EDITS = "sethlans_sequence_edits"
AUDIO = "sethlans_audio_edits"
RANGE_MODES = ("prepare_range", "prepare_audio")


class PrepFailure(RuntimeError):
    """A failure whose text this script composed: safe to report."""


SAFE = [PrepFailure]  # the loaded modules add their own failure classes (ShotsError, EditsError, FactsError)


def fail(msg):
    raise PrepFailure(msg)


def error_text(exc):
    """Text Sethlans composed is reported; anything else only by its class name (its message can carry a path)."""
    if isinstance(exc, tuple(SAFE)):
        return str(exc)[:2048]
    return f"Unreal prep pass failed ({type(exc).__name__})."


def all_nodes(graph, cls):
    out = []
    for branch in graph.get_branch_names():
        out += [n for n in graph.get_nodes_for_branch(cls, branch, False) if n not in out]
    return out


def load_required(name, what, error):
    """FR-SW-4: the shots or edits module; required, so one that does not load fails the prep (class name only)."""
    try:  # one fixed file; nothing is added to sys.path, so nothing else can be imported in its place
        spec = importlib.util.spec_from_file_location(name, os.path.join(P["scripts_dir"], name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        fail(f"The {what} module did not load ({type(exc).__name__}).")  # never the message: it can carry a path
    SAFE.append(getattr(module, error, PrepFailure))
    return module


def read_facts(preset, kind):
    """FR-GC-1, FR-GC-3: what the loaded preset renders with; scalability 4 and ``unknown`` when unreadable.

    Returns the facts module, or None when it did not load."""
    RES["scalability"], RES["game_overrides"], RES["texture_streaming"] = 4, "unknown", "unknown"
    try:  # one fixed file; nothing is added to sys.path, so nothing else can be imported in its place
        spec = importlib.util.spec_from_file_location(FACTS, os.path.join(P["scripts_dir"], FACTS + ".py"))
        facts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(facts)
    except Exception as exc:
        RES["warnings"].append(f"The preset facts module did not load ({type(exc).__name__}).")  # never the message
        return None
    SAFE.append(getattr(facts, "FactsError", PrepFailure))
    RES["game_overrides"], RES["texture_streaming"] = facts.game_overrides(preset, kind, RES["warnings"])
    if kind == "graph":  # the duplicates keep the preset's Game Overrides nodes unchanged
        RES["scalability"] = facts.graph_scalability(preset, RES["warnings"])
    return facts


def graph_layers(graph):
    layers = 0
    for branch in graph.get_branch_names():
        rl = [n for n in graph.get_nodes_for_branch(unreal.MovieGraphRenderLayerNode, branch, False) if not n.is_disabled()]
        passes = [n for n in graph.get_nodes_for_branch(unreal.MovieGraphRenderPassNode, branch, False)
                  if not n.is_disabled()]
        if rl:
            layers += len(rl) * max(1, len(passes))  # each enabled layer writes one file per pass
    return layers


def legacy_passes(preset):
    return len([s for s in preset.get_all_settings() if isinstance(s, unreal.MoviePipelineRenderPass) and s.is_enabled()])


def override_graph(dup, start, end):
    gos = all_nodes(dup, unreal.MovieGraphGlobalOutputSettingNode)
    if not gos:
        fail("Preset has no Global Output Settings node.")
    for node in gos:
        for edge, value in (("start", start), ("end", end)):
            bound = unreal.MovieGraphSequencePlaybackRangeBound()
            bound.type, bound.value = unreal.MovieGraphSequenceRangeType.CUSTOM, int(value)
            node.set_editor_property(f"override_custom_playback_range_{edge}", True)
            node.set_editor_property(f"custom_playback_range_{edge}", bound)
        node.set_editor_property("override_output_directory", True)
        node.set_editor_property("output_directory", unreal.DirectoryPath(path=P["output_dir"]))


def override_legacy(dup, start, end):
    out = dup.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    out.set_editor_property("use_custom_playback_range", True)
    out.set_editor_property("custom_start_frame", int(start))
    out.set_editor_property("custom_end_frame", int(end))
    out.set_editor_property("output_directory", unreal.DirectoryPath(P["output_dir"]))
    out.set_editor_property("file_name_format", P["file_name_format"])


def duplicate(eal, edit, kind, index, start, end):
    """One farm duplicate over ``[start, end)``: range and directory here, then ``edit`` (FR-SW-3, FR-SA-2)."""
    dup = eal.duplicate_asset(P["preset"], f"{P['package_root']}/MRG_Job{P['job_id']}_{index}")
    if dup is None:
        fail("Could not duplicate the preset.")
    (override_graph if kind == "graph" else override_legacy)(dup, start, end)
    RES.update(edit(dup, kind, P, RES["warnings"]))  # format (or sound), D5b, size, draft; never the original
    layers = graph_layers(dup) if kind == "graph" else legacy_passes(dup)
    if layers != 1 and (kind == "graph" or layers > 1):
        fail(f"Preset renders {layers} layers; phase 1 supports one.")
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


def in_cut(shots, seq, frame, first, last, what="Frame"):
    if not first <= frame < last:
        fail(f"{what} {frame} is outside the playback range [{first}, {last}).")
    if not shots.frame_in_cut(seq, frame):
        fail(f"{what} {frame} is not inside a camera cut of {P['sequence']}.")


def job_ranges(shots, seq, first, last):
    """The ``[start, end)`` of each queue job: one frame each (pilot: one per shot, FR-UE-25), or a piece's range."""
    if P["mode"] in RANGE_MODES:  # FR-SW-3, FR-SA-2: the first kept frame lies in a shot or camera cut
        lo, hi = int(P["range"][0]), int(P["range"][1])
        if not first <= lo < hi <= last:
            fail(f"Frames {lo}-{hi - 1} are outside the playback range [{first}, {last}).")
        in_cut(shots, seq, int(P["start"]), first, last)
        return [(lo, hi)]
    if P["mode"] != "prepare_pilot":
        in_cut(shots, seq, int(P["frame"]), first, last)
        return [(int(P["frame"]), int(P["frame"]) + 1)]
    asked = [int(f) for f in P.get("pilot_frames") or ()]  # a sequence pilot renders the manager's frames (FR-SR-1)
    frames = RES["pilot_frames"] = asked or shots.pilot_frames(seq, first, last)
    if not frames:
        fail(f"No camera cut found in {P['sequence']}; cannot measure.")
    if len(frames) > int(P.get("max_pilot_frames", 256)):
        fail(f"Sequence has {len(frames)} shots; the pilot supports at most {P.get('max_pilot_frames', 256)}.")
    for frame in asked:
        in_cut(shots, seq, frame, first, last, "Pilot frame")
    return [(f, f + 1) for f in frames]


def search_assets():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.search_all_assets(True)
    registry.wait_for_completion()


def playback(seq):
    """The playback range ``[first, last)``; the result also gets the display rate (FR-SD-5, FR-SA-5)."""
    first, last, rate = seq.get_playback_start(), seq.get_playback_end(), seq.get_display_rate()
    RES["playback_range"], RES["frame_rate"] = [first, last], [int(rate.numerator), int(rate.denominator)]
    return first, last


def load(what, path):
    asset = unreal.load_asset(path)
    if asset is None:
        fail(f"{what} {path} not found.")
    return asset


def prepare():
    shots = load_required(SHOTS, "shots", "ShotsError")
    edits = load_required(EDITS, "edits", "EditsError")
    if P["mode"] == "prepare_audio":  # FR-SA-2: the sound-only duplicate; it uses the edits module's helpers
        edit = load_required(AUDIO, "audio edits", "AudioEditsError").using(edits)
    else:
        edit = edits.apply
    eal = unreal.EditorAssetLibrary
    search_assets()
    root = P["package_root"]
    if eal.does_directory_exist(root) and eal.list_assets(root, True, False):
        fail(f"Package root {root} already exists.")
    preset = load("Preset", P["preset"])
    kind = preset_kind(preset)
    RES["preset_kind"] = kind
    read_facts(preset, kind)
    seq = load("Sequence", P["sequence"])
    first, last = playback(seq)
    RES["camera_cut_starts"] = shots.cut_starts(seq)
    ranges = job_ranges(shots, seq, first, last)
    if P.get("draft_cvars"):  # D11: draft CVars the preset's own console variables set again at render time
        RES["draft_conflicts"] = edits.draft_conflicts(preset, kind, P["draft_cvars"], RES["warnings"])
    queue = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        f"Q_Job{P['job_id']}", root, unreal.MoviePipelineQueue, None)
    for index, (start, end) in enumerate(ranges):
        dup = duplicate(eal, edit, kind, index, start, end)
        job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
        job.job_name = f"sethlans_{P['job_id']}_{start}"
        job.author = "Sethlans"  # EXR metadata unreal/jobAuthor; else the OS user name reaches the frames
        job.map = unreal.SoftObjectPath(P["map"])
        job.sequence = unreal.SoftObjectPath(P["sequence"])
        (job.set_graph_preset if kind == "graph" else job.set_preset_origin)(dup)
    eal.save_loaded_asset(queue, False)
    RES["queue_jobs"] = len(ranges)
    RES["effective_cvars"] = {n: unreal.SystemLibrary.get_console_variable_string_value(n) for n in P.get("cvar_names", [])}
    RES["queue_asset"] = f"{root}/Q_Job{P['job_id']}"
    RES["ok"] = True


def discover():
    """Mode ``list_shots`` (FR-SD-4, FR-SD-5): what the person chooses from; reads only: no queue asset, no save."""
    shots = load_required(SHOTS, "shots", "ShotsError")
    search_assets()
    preset = load("Preset", P["preset"])
    kind = preset_kind(preset)
    RES["preset_kind"] = kind
    facts = read_facts(preset, kind)
    if facts is None:
        fail("The preset facts module did not load.")
    seq = load("Sequence", P["sequence"])
    first, last = playback(seq)
    source, found = shots.list_shots(seq, first, last, RES["warnings"])
    if len(found) > int(P["max_shots"]):  # the list is not written: the result stays small
        fail(f"Sequence has {len(found)} shots; at most {P['max_shots']} are supported.")
    RES["shot_source"], RES["shots"] = source, found
    out = facts.output_facts(preset, kind)  # FactsError with fixed text when unreadable
    out["layers_ok"] = graph_layers(preset) == 1 if kind == "graph" else legacy_passes(preset) <= 1
    out["game_overrides"], out["texture_streaming"] = RES["game_overrides"], RES["texture_streaming"]
    RES["preset_facts"] = out
    RES["ok"] = True


def main():
    global P
    with open(os.environ["SETHLANS_UE_JOB_PARAMS"], encoding="utf-8") as handle:
        P = json.load(handle)
    try:
        (discover if P["mode"] == "list_shots" else prepare)()
    except Exception as exc:
        RES["ok"], RES["error"] = False, error_text(exc)
        raise
    finally:
        RES["prep_s"], RES["python"] = round(time.time() - T0, 2), sys.version.split()[0]
        RES["warnings"] = RES["warnings"][:64]  # the worker reads at most 64; the result stays small
        with open(P["result_path"], "w", encoding="utf-8") as handle:
            json.dump(RES, handle, indent=1)
        unreal.log(f"[SethlansPrep] wrote {P['result_path']} ok={RES['ok']}")


main()
