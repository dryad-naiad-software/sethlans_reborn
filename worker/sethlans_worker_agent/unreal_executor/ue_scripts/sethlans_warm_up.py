# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sethlans warm-up (Unreal foundation FR-UE-9, FR-UE-19, FR-UE-25).

Runs inside UE 5.8's embedded Python (3.11) with ``-ExecCmds="py <this file>"``
in an editor session (it needs a real editor world and Sequencer). Imports
only unreal, json, os, sys, time and pathlib; job data comes only from the
params file named by ``SETHLANS_UE_JOB_PARAMS`` (mode ``warmup``).

1. sets the preset's scalability **explicitly** (``Scalability <n>`` and every
   ``sg.*`` group): under the farm profile the editor starts at Epic (3),
   while the render uses the preset's level (Cinematic, 4, for the PoC);
2. loads every ``/Script/OptimusCore.OptimusDeformer`` asset (a missing class
   means zero deformers);
3. loads the map, opens the sequence and visits every ``step_frames`` stride
   frame, the first frame of every camera cut and the job's own frame(s),
   holding each ``step_seconds`` (time is set with ``set_global_position``,
   the 5.8 replacement of the deprecated ``set_current_time``);
4. holds ``hold_seconds``, logs ``[Warm] done, quitting`` (the success
   marker), writes ``result_path`` and quits the editor (``quit_editor`` in a
   ``finally``).
"""
import json
import os
import sys
import time

import unreal

P = {}
T0 = time.time()
SG_GROUPS = ("ViewDistanceQuality", "AntiAliasingQuality", "ShadowQuality", "GlobalIlluminationQuality",
             "ReflectionQuality", "PostProcessQuality", "TextureQuality", "EffectsQuality", "FoliageQuality",
             "ShadingQuality", "LandscapeQuality")
state = {"i": 0, "last": 0.0, "hold_from": None, "done": False, "handle": None, "visits": [], "deformers": 0}
keep = []


def warm_log(msg):
    unreal.log(f"[Warm] {msg}")


def command(text):
    unreal.SystemLibrary.execute_console_command(None, text)


def set_scalability(level):
    level = max(0, min(4, int(level)))
    command(f"Scalability {level}")
    for group in SG_GROUPS:
        command(f"sg.{group} {level}")
    warm_log(f"scalability {level}")


def load_deformers():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.wait_for_completion()
    try:
        path = unreal.TopLevelAssetPath("/Script/OptimusCore", "OptimusDeformer")
        found = registry.get_assets_by_class(path, True)
    except Exception:
        found = []
    for data in found:
        keep.append(data.get_asset())
        warm_log(f"loaded {data.package_name}")
    warm_log(f"{len(keep)} deformers loaded")
    return len(keep)


def visit_list(first, last):
    step = max(1, int(P.get("step_frames", 24)))
    visits = set(range(first, last, step))
    for key in ("camera_cut_starts", "frames"):
        visits.update(int(f) for f in P.get(key, []) if first <= int(f) < last)
    return sorted(visits)


def set_time(frame):
    lib = unreal.LevelSequenceEditorBlueprintLibrary
    try:
        params = unreal.MovieSceneSequencePlaybackParams()
        params.frame = unreal.FrameTime(unreal.FrameNumber(int(frame)))
        params.position_type = unreal.MovieScenePositionType.FRAME
        params.update_method = unreal.UpdatePositionMethod.SCRUB
        lib.set_global_position(params, unreal.MovieSceneTimeUnit.DISPLAY_RATE)
    except AttributeError:
        lib.set_current_time(int(frame))


def write_result(ok, error=""):
    try:
        with open(P["result_path"], "w", encoding="utf-8") as handle:
            json.dump({"ok": ok, "error": error[:2048], "visits": len(state["visits"]), "deformers": state["deformers"],
                       "script_s": round(time.time() - T0, 1), "python": sys.version.split()[0]}, handle)
    except Exception as exc:
        unreal.log_warning(f"[Warm] result write failed: {exc}")


def finish(ok, error=""):
    if state["done"]:
        return
    state["done"] = True
    try:
        if state["handle"] is not None:
            unreal.unregister_slate_post_tick_callback(state["handle"])
        if ok:
            warm_log("done, quitting")
        else:
            unreal.log_error(f"[Warm] failed: {error}")
        write_result(ok, error)
    finally:
        unreal.SystemLibrary.quit_editor()


def tick(_delta):
    if state["done"]:
        return
    try:
        now = time.time()
        if state["hold_from"] is None:
            if now - state["last"] < float(P.get("step_seconds", 4)):
                return
            state["last"] = now
            frame = state["visits"][state["i"]]
            set_time(frame)
            warm_log(f"frame {frame}")
            state["i"] += 1
            if state["i"] >= len(state["visits"]):
                state["hold_from"] = now
                warm_log("stepped through, holding")
        elif now - state["hold_from"] > float(P.get("hold_seconds", 240)):
            finish(True)
    except Exception as exc:
        finish(False, str(exc))


def main():
    global P
    try:
        with open(os.environ["SETHLANS_UE_JOB_PARAMS"], encoding="utf-8") as handle:
            P = json.load(handle)
        set_scalability(P.get("scalability", 4))
        state["deformers"] = load_deformers()
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(P["map"])
        warm_log("level loaded")
        sequence = unreal.load_asset(P["sequence"])
        if sequence is None:
            raise RuntimeError(f"Sequence {P['sequence']} not found.")
        unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(sequence)
        set_scalability(P.get("scalability", 4))  # again, after the level and Sequencer came up
        first, last = sequence.get_playback_start(), sequence.get_playback_end()
        state["visits"] = visit_list(first, last)
        if not state["visits"]:
            raise RuntimeError("Nothing to visit in the sequence's playback range.")
        warm_log(f"stepping frames {first} to {last} ({len(state['visits'])} visits)")
        state["handle"] = unreal.register_slate_post_tick_callback(tick)
    except Exception as exc:
        finish(False, str(exc))


main()
