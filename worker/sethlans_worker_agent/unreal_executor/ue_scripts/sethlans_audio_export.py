# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sethlans sound-source export (sequence rendering D23, FR-SA-9): the raw sources of an audio run.

Runs in UE 5.8's embedded Python (3.11) as the PythonScript commandlet (``-run=pythonscript -script=<this file>``,
no GPU); imports only unreal, importlib, json, math, os, sys and time; loads ``sethlans_shots.py`` and
``sethlans_sound_rules.py`` from ``scripts_dir`` by file path, never through ``sys.path``. Job data comes only
from the params file named by ``SETHLANS_UE_JOB_PARAMS`` (mode ``export_audio``). It **reads and exports
only**: it never sets a property and never saves an asset.

For every enabled audio section of the sequence's top-level audio tracks, and of the audio tracks inside its shot
and sub sequences (mapped to edit frames with the shot's time mapping and clipped to the shot section), that
overlaps ``range`` ``[r0, end + 1)``, ``result_path`` gets one row: ``start`` and ``end`` (edit frames; a start
between two frames is rounded up and the difference added to the start offset), ``start_offset_samples`` (in
samples of the source wave at its own rate), ``volume`` (the constant one), ``looping``, ``wav`` (``src_<n>.wav``
in ``export_dir``, each wave exported once with ``SoundExporterWAV``: its raw imported PCM) and ``sample_rate``.
``looping`` and how long a one-shot sound plays on follow UE 5.8.4 (``sethlans_sound_rules``): a section whose
sound plays on past its end ends where its wave ends (its ``duration``), when that is later; an unknown When
Finished mode is unsupported. Every Unreal name is UE 5.8.4's Python name and
nothing falls back silently (#283).
A section the worker cannot mix is a row ``{"unsupported": reason}`` (a keyed volume, a pitch other than 1, an
open range, a sound cue, a MetaSound or another non-wave source, a sound attached to an object). More than
``max_sections`` rows, or exports above ``max_export_bytes``, fail the step. Errors carry fixed text or a class
name, never a path. Track mute state is not readable from Python in 5.8.4 (probe): muted tracks are read.
"""
import importlib.util
import json
import math
import os
import sys
import time

import unreal

T0 = time.time()
RES = {"ok": False, "warnings": [], "sections": []}
P = {}
SHOTS = "sethlans_shots"
WAVE = "SoundWave"
MUTE_WARNING = "Mute state of audio tracks is not read; muted sounds are mixed."
RULES = "sethlans_sound_rules"  # how long and how often a sound plays in UE 5.8.4
PROP_MISSING = "Unreal has no property {name} on {cls}; the sound sources were not read."


class ExportFailure(RuntimeError):
    """A failure whose text this script composed: safe to report."""


SAFE = [ExportFailure]


def fail(msg):
    raise ExportFailure(msg)


def error_text(exc):
    """Text Sethlans composed is reported; anything else only by its class name (its message can carry a path)."""
    if isinstance(exc, tuple(SAFE)):
        return str(exc)[:2048]
    if isinstance(exc, AttributeError) and isinstance(exc.name, str) and exc.name.isidentifier():
        return f"The sound export failed (AttributeError: no {exc.name})."  # the missing name only (#283)
    return f"The sound export failed ({type(exc).__name__})."


def load(name, what, error):
    """One helper module from ``scripts_dir`` (the shots, the sound rules); one that does not load fails."""
    try:  # one fixed file; nothing is added to sys.path, so nothing else can be imported in its place
        spec = importlib.util.spec_from_file_location(name, os.path.join(P["scripts_dir"], name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        fail(f"The {what} module did not load ({type(exc).__name__}).")
    SAFE.append(getattr(module, error, ExportFailure))
    return module


def channel(sec, prefix):
    """``(constant value, keyed)`` of the section's channel whose name starts with ``prefix``; ``(1.0, False)``.

    UE 5.8.4's ``get_default()`` gives 0.0 for a channel with no default (``MovieSceneScriptingFloat.h``), so the
    default is read only when ``has_default()``; with none the channel sets nothing and the sound plays at 1."""
    for ch in sec.get_all_channels():
        if str(ch.get_name()).startswith(prefix):
            value = float(ch.get_default()) if ch.has_default() else 1.0
            return value, len(ch.get_keys()) > 0
    return 1.0, False


def reason_of(sec, sound):
    """Why a section cannot be mixed, or ``""``."""
    if sound is None:
        return "no sound"
    name = sound.get_class().get_name()
    if name != WAVE:
        return "a sound cue" if "Cue" in name else "a MetaSound source" if "MetaSound" in name else "not a sound wave"
    if channel(sec, "Volume")[1]:
        return "a keyed volume"
    pitch, keyed = channel(sec, "Pitch")
    if keyed or pitch != 1.0:
        return "a pitch other than 1"
    if not (sec.has_start_frame() and sec.has_end_frame()):
        return "an open range"
    return ""


def prop(obj, name):
    """An editor property; one the class does not have fails with fixed text naming it (never a guess, #283)."""
    try:
        return obj.get_editor_property(name)
    except Exception:
        raise ExportFailure(PROP_MISSING.format(name=name, cls=type(obj).__name__)) from None


def wave_rate(sound):
    """The wave's imported rate (editor data, the raw PCM's rate); a wave without one (0) gives its playback rate."""
    for name in ("imported_sample_rate", "sample_rate"):
        value = int(prop(sound, name))
        if value > 0:
            return value
    fail("A sound wave has no sample rate.")


class Exports:
    """Each wave exported once, as ``src_<n>.wav``; the total size is capped."""

    def __init__(self):
        self.names, self.total = {}, 0

    def name_of(self, sound):
        path = sound.get_path_name()
        if path not in self.names:
            name = f"src_{len(self.names):03d}.wav"
            target = os.path.join(P["export_dir"], name)
            task = unreal.AssetExportTask()
            task.object, task.filename, task.exporter = sound, target, unreal.SoundExporterWAV()
            task.automated, task.prompt, task.replace_identical = True, False, True
            if not unreal.Exporter.run_asset_export_task(task) or not os.path.isfile(target):
                fail("A sound wave could not be exported.")
            self.total += os.path.getsize(target)
            if self.total > int(P["max_export_bytes"]):
                fail("The sound sources exceed the farm's export limit.")
            self.names[path] = name
        return self.names[path]


def audio_sections(seq, shots):
    out = []
    for track in seq.get_tracks():
        if isinstance(track, unreal.MovieSceneAudioTrack):
            out += [sec for sec in track.get_sections() if shots.active(sec)]
    return out


def section_end(rules, sec, sound, owner, lo, base):
    """The section's end in the owner's frames, past its own end while a one-shot wave plays on (``rules``:
    ``sethlans_sound_rules``); a text when UE's behaviour is not known (the row is then unsupported)."""
    hi = float(sec.get_end_frame())
    keeps = rules.keeps_playing(sec, owner, RES["warnings"])
    if isinstance(keeps, str) or not keeps:
        return keeps if isinstance(keeps, str) else hi
    rate = owner.get_display_rate()
    return max(hi, lo + (float(prop(sound, "duration")) - base) * rate.numerator / rate.denominator)


def _span(rules, sec, sound, owner, base, reason):
    """``(lo, hi, looped)`` in the owner's frames; ``hi`` is the unsupported reason when UE's end is not known."""
    lo, looped = float(sec.get_start_frame()), False if reason else rules.loops(sec, sound)
    hi = float(sec.get_end_frame()) if reason or looped else section_end(rules, sec, sound, owner, lo, base)
    return lo, hi, looped


def row_of(rules, sec, owner, mapping, spf, exports):
    """One row, or ``None`` outside the range; ``mapping``: ``(start, end, inner_start, offset, ratio)`` or None."""
    sound = prop(sec, "sound")
    reason = reason_of(sec, sound)
    if reason == "an open range":
        return {"unsupported": reason}
    if reason == "no sound":
        return None
    ticks = owner.get_tick_resolution()
    base = prop(sec, "start_frame_offset").value * ticks.denominator / ticks.numerator
    lo, hi, looped = _span(rules, sec, sound, owner, base, reason)
    if isinstance(hi, str):
        return {"unsupported": hi}
    window = (-math.inf, math.inf)
    if mapping is not None:
        start, end, inner_start, offset, ratio = mapping
        lo, hi = start + (lo - inner_start - offset) / ratio, start + (hi - inner_start - offset) / ratio
        window = (start, end)
    first, last = math.ceil(max(lo, window[0])), math.ceil(min(hi, window[1]))
    if first >= last or first >= int(P["range"][1]) or last <= int(P["range"][0]):
        return None
    if reason:
        return {"unsupported": reason}
    rate = wave_rate(sound)
    volume = channel(sec, "Volume")[0]
    return {"start": first, "end": last, "start_offset_samples": max(0, round((base + (first - lo) * spf) * rate)),
            "volume": min(max(volume, 0.0), 4.0), "looping": looped, "wav": exports.name_of(sound),
            "sample_rate": rate}


def bound_sections(seq):
    """Audio sections attached to objects (spatialised): counted, never mixed."""
    rows = []
    for binding in seq.get_bindings():
        for track in binding.get_tracks():
            if isinstance(track, unreal.MovieSceneAudioTrack):
                rows += [{"unsupported": "a sound attached to an object"} for _sec in track.get_sections()]
    return rows


def export():
    shots, rules = load(SHOTS, "shots", "ShotsError"), load(RULES, "sound rules", "SoundRulesError")
    unreal.AssetRegistryHelpers.get_asset_registry().search_all_assets(True)
    seq = unreal.load_asset(P["sequence"])
    if seq is None:
        fail(f"Sequence {P['sequence']} not found.")
    rate = seq.get_display_rate()
    spf = rate.denominator / rate.numerator  # seconds per edit frame
    exports, rows = Exports(), []
    pairs = [(sec, seq, None) for sec in audio_sections(seq, shots)]
    for start, end, sub, inner_start, offset, ratio in shots.shot_sections(seq):
        pairs += [(sec, sub, (start, end, inner_start, offset, ratio)) for sec in audio_sections(sub, shots)]
    for sec, owner, mapping in pairs:
        row = row_of(rules, sec, owner, mapping, spf, exports)
        if row is not None:
            rows.append(row)
        if len(rows) > int(P["max_sections"]):
            fail(f"The run has more than {P['max_sections']} audio sections; the sound offset is not measured.")
    rows += bound_sections(seq)
    if len(rows) > int(P["max_sections"]):
        fail(f"The run has more than {P['max_sections']} audio sections; the sound offset is not measured.")
    RES["sections"] = rows
    if pairs:
        RES["warnings"].append(MUTE_WARNING)
    RES["ok"] = True


def main():
    global P
    with open(os.environ["SETHLANS_UE_JOB_PARAMS"], encoding="utf-8") as handle:
        P = json.load(handle)
    try:
        if P.get("mode") != "export_audio":
            fail("The sound export was started in the wrong mode.")
        export()
    except Exception as exc:
        RES["ok"], RES["error"], RES["sections"] = False, error_text(exc), []
        raise
    finally:
        RES["export_s"], RES["python"] = round(time.time() - T0, 2), sys.version.split()[0]
        with open(P["result_path"], "w", encoding="utf-8") as handle:
            json.dump(RES, handle, indent=1)
        unreal.log(f"[SethlansAudioExport] wrote {P['result_path']} ok={RES['ok']}")


main()
