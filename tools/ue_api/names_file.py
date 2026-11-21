# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The committed list of verified Unreal Python names (issue #283).

``tests/unit/worker/unreal/ue_api/ue_api_names.json``::

    {"schema": 1, "ue": "5.8.4",
     "names": {"unreal" | "static" | "properties" | "members":
               {name: {"used_by": ["scripts", "stubs"], "source": "...", "unreal": "..."}}}}

* ``used_by``: the worker's UE-side scripts (``extract_names.py``) and/or the unit tests' ``unreal`` stubs;
* ``source``: where UE 5.8.4's source declares the name (``check_source.py``);
* ``unreal``: what a real UE 5.8.4 reported for it (``verify_in_unreal.py``, merged with ``merge_ue_report``).

A name is verified only when a real UE run confirmed it (``unreal`` evidence); ``source`` evidence is a first
check. ``update`` rewrites the list from the names in use, keeping the evidence already recorded; a name nothing
uses any more is dropped.
"""
import json
from pathlib import Path

CATEGORIES = ("unreal", "static", "properties", "members")
UE_VERSION = "5.8.4"


def load(path):
    path = Path(path)
    if not path.exists():
        return {"schema": 1, "ue": UE_VERSION, "names": {c: {} for c in CATEGORIES}}
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    data["names"] = {c: dict(sorted(data["names"].get(c, {}).items())) for c in CATEGORIES}
    Path(path).write_text(json.dumps(data, indent=1, sort_keys=False) + "\n", encoding="utf-8")


def update(path, scripts, stubs):
    """``scripts`` / ``stubs``: ``{category: [names]}``; returns the names that still need evidence."""
    data = load(path)
    for category in CATEGORIES:
        old = data["names"].get(category, {})
        new = {}
        for name in sorted(set(scripts.get(category, ())) | set(stubs.get(category, ()))):
            entry = dict(old.get(name, {"source": "", "unreal": ""}))
            entry["used_by"] = [who for who, names in (("scripts", scripts), ("stubs", stubs))
                                if name in names.get(category, ())]
            new[name] = entry
        data["names"][category] = new
    save(path, data)
    return unverified(data)


def merge(path, field, evidence):
    """Record ``evidence`` (``{category: {name: text}}``) in ``field`` (``source`` or ``unreal``)."""
    data = load(path)
    for category, found in evidence.items():
        for name, text in found.items():
            if name in data["names"].get(category, {}):
                data["names"][category][name][field] = str(text)
    save(path, data)
    return unverified(data)


def merge_ue_report(path, report):
    """A ``verify_in_unreal.py`` report: a name UE has gets ``unreal`` evidence; a missing one gets ``MISSING``."""
    evidence = {c: {} for c in CATEGORIES}
    for category in CATEGORIES:
        for name, result in report.get(category, {}).items():
            found = result.get("found_on") or ([result["type"]] if result.get("ok") else [])
            evidence[category][name] = f"UE {report.get('build', UE_VERSION)}: " + ", ".join(found[:3]) if found \
                else "MISSING"
    return merge(path, "unreal", evidence)


def unverified(data):
    """``category:name`` of every name a real UE run has not confirmed (no ``unreal`` evidence, or ``MISSING``).

    Source evidence alone is not enough: it let #283's wrong names through."""
    return [f"{c}:{name}" for c in CATEGORIES for name, entry in data["names"].get(c, {}).items()
            if not entry.get("unreal") or entry.get("unreal") == "MISSING"]


def names(data, category):
    return set(data["names"].get(category, {}))


def stub_names():
    """The names the unit tests' ``unreal`` stub exposes (imported lazily: only the tests and ``--write`` need it)."""
    from tests.unit.worker.unreal.ue_api._stub_names import stub_names as collect
    return collect()


def main(argv=None):
    """``python -m tools.ue_api.names_file --merge-ue-report report.json``: record a real UE run's evidence."""
    import argparse
    from tools.ue_api.extract_names import DATA_FILE
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--merge-ue-report", required=True, help="a verify_in_unreal.py report")
    args = parser.parse_args(argv)
    left = merge_ue_report(DATA_FILE, json.loads(Path(args.merge_ue_report).read_text(encoding="utf-8")))
    print(json.dumps({"unverified": left}, indent=1))
    return 1 if left else 0


if __name__ == "__main__":
    raise SystemExit(main())
