# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check the listed Unreal names against UE 5.8.4's source and record the evidence (issue #283).

Scans the engine's reflected declarations (``UCLASS``/``USTRUCT``/``UENUM`` with their ``ScriptName``,
``UPROPERTY``, ``UFUNCTION`` with ``ScriptName``/``ScriptMethod``, ``UENUM`` values) and the Python plugin's own
method tables (``load_asset``, ``get_editor_property``, ...). Python names are compared case- and
underscore-insensitively with a bool's leading ``b`` dropped (``bOverride_OutputResolution`` is
``override_output_resolution``). A class whose ``ScriptName`` differs from the name used is a mismatch.

``static`` names must be declared on the class they are read on; ``properties`` and ``members`` are matched on
any reflected class (the owners are recorded; ``verify_in_unreal.py`` checks them on the real objects).

    python -m tools.ue_api.check_source [--engine "C:/Program Files/Epic Games/UE_5.8/Engine"] [--write]
"""
import argparse
import json
import re
import sys
from pathlib import Path

from tools.ue_api import extract_names, names_file

DEFAULT_ENGINE = Path(r"C:\Program Files\Epic Games\UE_5.8\Engine")
SUBDIRS = ("Source/Runtime", "Source/Editor", "Source/Developer", "Plugins/MovieScene",
           "Plugins/Editor/EditorScriptingUtilities", "Plugins/Experimental/PythonScriptPlugin")
RELEVANT = ("MovieRenderPipeline", "SequencerScripting", "Runtime/MovieScene/", "MovieSceneTracks", "LevelSequence",
            "CoreUObject", "AssetRegistry", "AssetTools", "EditorScriptingUtilities", "Engine/Classes/Sound",
            "AssetExportTask", "Exporters", "LevelEditor")
MACRO = re.compile(r"\b(UCLASS|USTRUCT|UENUM|UPROPERTY|UFUNCTION)\s*\(")
DECL = re.compile(r"^\s*(?:class|struct|enum class|enum)\s+(?:[A-Z_0-9]+_API\s+)?([UFEA]?)([A-Za-z]\w*)")
META = re.compile(r'\b(ScriptName|ScriptMethod)\s*=\s*"([^"]+)"')
PROP = re.compile(r"([A-Za-z_]\w*)\s*(?::\s*\d+)?\s*(?:=[^;]*)?(?:\[[^\]]*\])?\s*;")
FUNC = re.compile(r"([A-Za-z_]\w*)\s*\(")
PY_TABLE = re.compile(r'\{\s*"([a-z_][a-z0-9_]*)",\s*PyCFunctionCast')
ENUM_VALUE = re.compile(r"^\s*([A-Za-z]\w*)\s*(?:=|,|UMETA|$)")


def norm(name):
    return re.sub(r"^b(?=[A-Z])", "", str(name)).replace("_", "").lower()


class Index:
    """``classes``: Python class name -> [(C++ name, where)]; ``renamed``: C++ name -> ScriptName;
    ``members``: normalised member name -> [(kind, owner Python name, where)]."""

    def __init__(self, engine):
        self.engine, self.classes, self.renamed, self.members, self.python = Path(engine), {}, {}, {}, {}

    def build(self):
        for sub in SUBDIRS:
            for path in (self.engine / sub).rglob("*.h"):
                if not {"Intermediate", "ThirdParty"} & set(path.parts):
                    self._header(path)
        for path in (self.engine / "Plugins/Experimental/PythonScriptPlugin").rglob("*.cpp"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in PY_TABLE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                self.python.setdefault(match.group(1), f"{self._rel(path)}:{line}")
        return self

    def _rel(self, path):
        return path.relative_to(self.engine).as_posix()

    def _add(self, name, kind, owner, where):
        self.members.setdefault(norm(name), []).append((kind, owner, where))

    def _header(self, path):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        owner, pending, enum_owner = None, None, None
        for number, line in enumerate(lines, 1):
            where = f"{self._rel(path)}:{number}"
            found = MACRO.search(line)
            if found:
                pending, enum_owner = (found.group(1), line, where), None if found.group(1) != "UPROPERTY" else enum_owner
                continue
            if pending is None:
                value = ENUM_VALUE.match(line) if enum_owner else None
                if value:
                    self._add(value.group(1), "enum value", enum_owner, where)
                enum_owner = None if enum_owner and "};" in line else enum_owner
                continue
            if not line.strip() or line.strip().startswith("//"):
                continue
            owner, enum_owner = self._declaration(pending, line, owner, enum_owner)
            pending = None

    def _declaration(self, pending, line, owner, enum_owner):
        kind, macro_line, where = pending
        meta = dict(META.findall(macro_line))
        if kind in ("UCLASS", "USTRUCT", "UENUM"):
            decl = DECL.match(line)
            if decl:
                cpp = decl.group(2)
                owner = meta.get("ScriptName", cpp).split(";")[0]
                self.classes.setdefault(owner, []).append((decl.group(1) + cpp, where))
                if owner != cpp:
                    self.renamed[cpp] = owner
                enum_owner = owner if kind == "UENUM" else None
        elif kind == "UFUNCTION":
            func = FUNC.search(re.sub(r"<[^>]*>", "", line))
            for alias in ([func.group(1)] if func else []) + [p for v in meta.values() for p in v.split(";")]:
                self._add(alias, "function", owner, where)
        elif kind == "UPROPERTY":
            prop = PROP.search(line)
            for alias in ([prop.group(1)] if prop else []) + [p for p in meta.get("ScriptName", "").split(";") if p]:
                self._add(alias, "property", owner, where)
        return owner, enum_owner

    def top(self, name):
        if name in self.python:
            return f"Python function {self.python[name]}"
        if name in self.renamed:
            return f"MISMATCH: UE's Python name is {self.renamed[name]}"
        found = self.classes.get(name)
        return f"{found[0][0]} {found[0][1]}" if found else ""

    def member(self, name, owner=None):
        hits = sorted((h for h in self.members.get(norm(name), []) if owner is None or h[1] == owner), key=_rank)
        parts = [f"{kind} on {who} {where}" for kind, who, where in hits]
        if owner is None and name in self.python:  # the Python wrapper's own method (get_editor_property, ...)
            parts.insert(0, f"Python method {self.python[name]}")
        return "; ".join(parts[:3])


def _rank(hit):
    """Declarations in the modules the scripts use come first in the evidence."""
    return next((i for i, part in enumerate(RELEVANT) if part in hit[2]), len(RELEVANT))


def evidence(index, data):
    out = {"unreal": {}, "static": {}, "properties": {}, "members": {}}
    for name in data["names"]["unreal"]:
        out["unreal"][name] = index.top(name)
    for name in data["names"]["static"]:
        cls, member = name.split(".", 1)
        out["static"][name] = index.member(member, owner=cls)
    for category in ("properties", "members"):
        for name in data["names"][category]:
            out[category][name] = index.member(name)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default=str(DEFAULT_ENGINE))
    parser.add_argument("--write", action="store_true", help="record the evidence in the names file")
    args = parser.parse_args(argv)
    data = names_file.load(extract_names.DATA_FILE)
    found = evidence(Index(args.engine).build(), data)
    for category, names in found.items():
        for name, text in names.items():
            if not text or text.startswith("MISMATCH"):
                print(f"{category} {name}: {text or 'NOT FOUND'}")
    if args.write:
        clean = {c: {n: t for n, t in names.items() if t and not t.startswith("MISMATCH")} for c, names in found.items()}
        left = names_file.merge(extract_names.DATA_FILE, "source", clean)
        print(json.dumps({"unverified": left}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
