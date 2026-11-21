# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Unreal names the unit tests' ``unreal`` stub exposes (issue #283), in the categories of ``tools/ue_api``.

* ``unreal``: every public attribute of the stub module ``make_unreal`` builds;
* ``static``: ``X.y`` of every namespace on it (``SystemLibrary.quit_editor``, enum values, ...);
* ``members``: the public methods the stub classes define (sections, tracks, settings, nodes, structs, ...);
* ``properties``: the editor properties the strict stub classes declare (``DEFAULTS``).

Instance fields of stub structs are not listed (a stub sets them freely); their names are checked where the
scripts read them. Not a test module.
"""
import types

from tests.unit.worker.unreal import _ue_stub, _ue_stub_audio, _ue_stub_mrq, _ue_stub_seq

STUB_MODULES = (_ue_stub, _ue_stub_audio, _ue_stub_mrq, _ue_stub_seq)


def _classes():
    return [v for m in STUB_MODULES for v in vars(m).values() if isinstance(v, type) and v.__module__ == m.__name__]


def stub_names():
    module = _ue_stub.make_unreal({})
    out = {"unreal": set(), "static": set(), "members": set(), "properties": set()}
    for name in (n for n in dir(module) if not n.startswith("_")):
        out["unreal"].add(name)
        value = getattr(module, name)
        if isinstance(value, types.SimpleNamespace):
            out["static"] |= {f"{name}.{attr}" for attr in vars(value)}
    for cls in _classes():
        own = vars(cls)
        out["members"] |= {a for a, v in own.items() if callable(v) and not a.startswith("_") and a != "DEFAULTS"}
        out["properties"] |= set(own.get("DEFAULTS", {}))
    return {key: sorted(values) for key, values in out.items()}
