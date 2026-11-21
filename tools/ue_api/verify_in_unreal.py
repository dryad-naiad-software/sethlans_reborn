# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check every listed Unreal Python name inside a real Unreal Editor (issue #283). A dev tool, not the product.

Runs as the PythonScript commandlet (no render, no GPU work, nothing saved)::

    UnrealEditor-Cmd.exe <project.uproject> -run=pythonscript -script=<this file> -unattended -nosplash
        -log -UDPMESSAGING_TRANSPORT_ENABLE=0

with ``SETHLANS_UE_API_PARAMS`` naming a JSON file::

    {"names_path": ".../ue_api_names.json", "output_path": ".../ue_api_report.json",
     "sequence": "/Game/...", "presets": ["/Game/.../GraphPreset", "/Game/.../LegacyPreset"]}

It imports only ``unreal``, ``json``, ``os`` and ``sys``. For each name: ``unreal`` and ``static`` names report
``hasattr`` and the type; ``properties`` and ``members`` report the types of the probed objects that have them.
The probed objects are every listed class, its class default object or a fresh struct, and live objects reached
from the sequence and presets (tracks, sections, sub-sequences, channels, bindings, sounds, graph nodes, legacy
settings, console variable entries, the property values they hold), the asset registry and an asset's data, the
asset tools, and a transient queue and job (never saved). Every probe that fails is listed in ``errors``; a
property is probed only on the classes the scripts read it from, so a clean run has none. The report is always
written; then a missing name raises, so the commandlet exits non-zero, and 0 means every name was found. Merge
the report with ``python -m tools.ue_api.names_file --merge-ue-report <report.json>``.
"""
import json
import os
import sys

import unreal

P = {}
REPORT = {"errors": []}
# Structs an empty construction makes UE log a script error for (and the commandlet exit 1): built with valid values.
STRUCT_ARGS = {"TopLevelAssetPath": ("/Script/LevelSequence", "LevelSequence")}
OPTIONAL_PROBES = ("MovieGraphPropertyInfo", "MoviePipelineConsoleVariableEntry", "AssetExportTask",
                   "MovieSceneSequencePlaybackParams", "MovieGraphSequencePlaybackRangeBound")


def note(step, exc):
    REPORT["errors"].append(f"{step}: {type(exc).__name__}: {str(exc)[:300]}")


def safe(step, func, default=None):
    try:
        return func()
    except Exception as exc:  # a diagnostic tool: every failure is reported, none hidden
        note(step, exc)
        return default


def call(step, obj, method, *args, default=()):
    """``obj.method(*args)``, a missing method or a failing call reported under ``step``."""
    return safe(step, lambda: getattr(obj, method)(*args), default)


class Pool:
    """The objects names are probed on, one per type (the first one found)."""

    def __init__(self):
        self.objects, self.seen = [], set()

    def add(self, obj):
        if obj is None:
            return None
        key = ("class", obj.__name__) if isinstance(obj, type) else ("object", type(obj).__name__)
        if key not in self.seen:
            self.seen.add(key)
            self.objects.append(obj)
        return obj

    def add_all(self, items):
        for item in items or ():
            self.add(item)
        return list(items or ())


def _classes(pool, names):
    for name in names["unreal"]:
        value = getattr(unreal, name, None)
        if not isinstance(value, type):
            continue
        pool.add(value)
        if issubclass(value, getattr(unreal, "Object", ())):
            pool.add(safe(f"cdo {name}", lambda v=value: unreal.get_default_object(v)))
        elif issubclass(value, getattr(unreal, "StructBase", ())):  # with valid values where empty ones are errors
            pool.add(safe(f"struct {name}", lambda v=value, n=name: v(*STRUCT_ARGS.get(n, ()))))


def _is(obj, class_name):
    """``isinstance`` against an Unreal class that may not exist (then never)."""
    return isinstance(obj, getattr(unreal, class_name, ()))


def _sections(pool, owner, depth):
    for track in pool.add_all(call("tracks", owner, "get_tracks")):
        for section in pool.add_all(call("sections", track, "get_sections")):
            # each probe only where the scripts read it: channels and sound on audio sections, ``parameters`` on
            # shot and sub sections (MovieSceneSubSection)
            if _is(section, "MovieSceneAudioSection"):
                pool.add_all(call("channels", section, "get_all_channels"))
            params = safe("parameters", lambda s=section: s.get_editor_property("parameters")) \
                if _is(section, "MovieSceneSubSection") else None
            if params is not None:
                pool.add(params)
                pool.add(safe("time_scale", lambda p=params: p.get_editor_property("time_scale")))
                pool.add(safe("start_frame_offset", lambda p=params: p.get_editor_property("start_frame_offset")))
            if _is(section, "MovieSceneAudioSection"):
                pool.add(safe("sound", lambda s=section: s.get_editor_property("sound")))
            sub = call("sub sequence", section, "get_sequence", default=None) if hasattr(section, "get_sequence") else None
            if sub is not None and depth < 2:
                pool.add(sub)
                _sections(pool, sub, depth + 1)


def _sequence(pool):
    seq = pool.add(safe("load sequence", lambda: unreal.load_asset(P["sequence"])))
    if seq is None:
        return
    pool.add(call("display rate", seq, "get_display_rate", default=None))
    _sections(pool, seq, 0)
    for binding in pool.add_all(call("bindings", seq, "get_bindings")):
        pool.add_all(call("binding tracks", binding, "get_tracks"))


def _values(pool, obj, names):
    """The struct and object values ``obj`` holds under the listed property names."""
    for prop in names["properties"]:
        try:
            value = obj.get_editor_property(prop)
        except Exception:
            continue
        if hasattr(value, "get_editor_property") or hasattr(value, "x"):
            pool.add(value)


def _presets(pool, names):
    for path in P.get("presets", []):
        preset = pool.add(safe(f"load preset {path}", lambda p=path: unreal.load_asset(p)))
        if hasattr(preset, "get_branch_names"):
            for branch in call("branches", preset, "get_branch_names"):
                for node in safe("nodes", lambda b=branch: preset.get_nodes_for_branch(unreal.MovieGraphNode, b, False),
                                 []):
                    pool.add(node)
                    _values(pool, node, names)
                    pool.add_all(call("exposed", node, "get_exposed_properties"))
        elif hasattr(preset, "get_all_settings"):
            for setting in pool.add_all(call("settings", preset, "get_all_settings")):
                _values(pool, setting, names)
                if hasattr(setting, "get_console_variables"):
                    pool.add_all(call("cvar entries", setting, "get_console_variables"))


def _editor(pool):
    """Extra objects to probe on; a probe whose Unreal name this build lacks is skipped (it is not a listed name)."""
    registry = pool.add(call("asset registry", unreal.AssetRegistryHelpers, "get_asset_registry", default=None))
    if registry is not None and hasattr(registry, "get_assets_by_class"):
        path = safe("asset path", lambda: unreal.TopLevelAssetPath(*STRUCT_ARGS["TopLevelAssetPath"]))
        pool.add_all(safe("asset data", lambda: registry.get_assets_by_class(path, True)[:1], []))
    pool.add(call("asset tools", unreal.AssetToolsHelpers, "get_asset_tools", default=None))
    if hasattr(unreal, "new_object"):
        queue = pool.add(safe("transient queue", lambda: unreal.new_object(unreal.MoviePipelineQueue)))
        if queue is not None:
            pool.add(safe("transient job", lambda: queue.allocate_new_job(unreal.MoviePipelineExecutorJob)))
    pool.add(safe("named resolution", lambda: unreal.MovieGraphLibrary.named_resolution_from_size(640, 360)))
    for extra in (e for e in OPTIONAL_PROBES if hasattr(unreal, e)):
        pool.add(safe(f"new {extra}", lambda e=extra: getattr(unreal, e)()))


def _type(value):
    return value.__name__ if isinstance(value, type) else type(value).__name__


def check(names, pool):
    REPORT["unreal"] = {n: {"ok": hasattr(unreal, n), "type": _type(getattr(unreal, n, None))} for n in names["unreal"]}
    REPORT["static"] = {}
    for name in names["static"]:
        cls, member = name.split(".", 1)
        owner = getattr(unreal, cls, None)
        REPORT["static"][name] = {"ok": hasattr(owner, member), "type": _type(getattr(owner, member, None))}
    instances = [o for o in pool.objects if not isinstance(o, type)]
    REPORT["properties"] = {}
    for prop in names["properties"]:
        found = []
        for obj in instances:
            try:
                obj.get_editor_property(prop)
                found.append(_type(obj))
            except Exception:
                pass
        REPORT["properties"][prop] = {"found_on": found}
    REPORT["members"] = {m: {"found_on": [_type(o) for o in pool.objects if safe_has(o, m)]} for m in names["members"]}
    REPORT["pool"] = [_type(o) for o in pool.objects]


def missing(report):
    """The names this Unreal lacks: ``category:name``."""
    out = [f"{c}:{n}" for c in ("unreal", "static") for n, r in report.get(c, {}).items() if not r["ok"]]
    return out + [f"{c}:{n}" for c in ("properties", "members") for n, r in report.get(c, {}).items()
                  if not r["found_on"]]


def safe_has(obj, name):
    try:
        return hasattr(obj, name)
    except Exception:
        return False


def main():
    global P
    with open(os.environ["SETHLANS_UE_API_PARAMS"], encoding="utf-8") as handle:
        P = json.load(handle)
    with open(P["names_path"], encoding="utf-8") as handle:
        names = {c: sorted(v) for c, v in json.load(handle)["names"].items()}
    version = getattr(unreal.SystemLibrary, "get_engine_version", None)
    REPORT["build"] = safe("version", version, "unknown") if version is not None else "unknown"
    REPORT["python"] = sys.version.split()[0]
    pool = Pool()
    try:
        _classes(pool, names)
        _sequence(pool)
        _presets(pool, names)
        _editor(pool)
        check(names, pool)
    finally:
        with open(P["output_path"], "w", encoding="utf-8") as handle:
            json.dump(REPORT, handle, indent=1)
        unreal.log(f"[SethlansApiCheck] wrote {P['output_path']}")
    lacking = missing(REPORT)
    if lacking:  # the commandlet then exits non-zero; 0 means every listed name was found
        raise RuntimeError(f"{len(lacking)} listed Unreal names are missing: {', '.join(lacking[:20])}")


main()
