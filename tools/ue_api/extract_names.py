# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Unreal Python name the worker's UE-side scripts use, found with ``ast`` (issue #283).

Unreal's Python names are not its C++ names (a ``ScriptName`` renames a class), so every name the scripts use is
listed here mechanically and checked against UE (``verify_in_unreal.py``) and its source. ``extract()`` returns:

* ``unreal``: names read on the module, ``unreal.X`` or ``getattr(unreal, "X")`` (classes, enums, functions);
* ``static``: ``X.y`` read on such a name (static functions, enum values), ``unreal.X.y`` or ``getattr(unreal.X, "y")``;
* ``properties``: editor property names, ``get_editor_property`` / ``set_editor_property`` and every script helper
  that passes its argument on to one (found by a fixpoint, so ``override(node, "x", v)`` gives ``x`` and
  ``override_x``), resolved through literals, f-strings, ``"prefix" + name``, module constants and loop targets;
* ``members``: attributes and methods read or set on other objects (struct fields, sections, tracks, ...), less
  Python's own (``str``/``list``/``dict``/``set``/``tuple`` and the module loader) and the scripts' own names;
* ``dynamic``: what could not be resolved (``file:line``); the tests require none.

From the repository root, ``python -m tools.ue_api.extract_names`` prints the names; ``--write`` refreshes the test
data file (adding the stubs' names, keeping the evidence already recorded); then ``python -m
tools.ue_api.check_source --write`` records UE source evidence for new names.
"""
import argparse
import ast
import json
import sys
from pathlib import Path

from tools.ue_api.resolve import Resolver, module_constants

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "worker" / "sethlans_worker_agent" / "unreal_executor" / "ue_scripts"
DATA_FILE = ROOT / "tests" / "unit" / "worker" / "unreal" / "ue_api" / "ue_api_names.json"
STDLIB = {"json", "os", "sys", "time", "math", "importlib"}
PYTHON_NAMES = set().union(*(dir(t) for t in (str, list, dict, set, tuple))) | {"loader", "exec_module"}
BASE_HELPERS = {"get_editor_property": {(0, "")}, "set_editor_property": {(0, "")}}


def _call_name(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None


def _arg_index(call, index):
    """The argument a helper's name parameter receives: method calls pass ``self`` implicitly (index 0 is arg 0)."""
    return call.args[index] if index < len(call.args) else None


def _functions(trees):
    return [(name, f, consts) for name, tree, consts in trees for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)]


def _helpers(trees):
    """Fixpoint: a function that passes its parameter on to a property call is a property helper too."""
    helpers = {k: set(v) for k, v in BASE_HELPERS.items()}
    changed = True
    while changed:
        changed = False
        for _file, func, consts in _functions(trees):
            resolver = Resolver(consts, func)
            for call in (n for n in ast.walk(func) if isinstance(n, ast.Call) and _call_name(n) in helpers):
                for index, prefix in set(helpers[_call_name(call)]):
                    for item in resolver.resolve(_arg_index(call, index)) or () if _arg_index(call, index) else ():
                        if item[0] == "param":
                            entry = (item[1], prefix + item[2])
                            if entry not in helpers.setdefault(func.name, set()):
                                helpers[func.name].add(entry)
                                changed = True
    return helpers


def _properties(trees, helpers, out):
    for file, tree, consts in trees:
        funcs = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)]
        owner = {id(n): f for f in funcs for n in ast.walk(f)}
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) in helpers):
            resolver = Resolver(consts, owner.get(id(call)))
            for index, prefix in helpers[_call_name(call)]:
                arg = _arg_index(call, index)
                if arg is None:
                    continue
                got = resolver.resolve(arg)
                if got is None:
                    out["dynamic"].add(f"{file}:{call.lineno}")
                    continue
                out["properties"].update(prefix + item[1] for item in got if item[0] == "str")


def _getattr_call(call):
    """``getattr(obj, name, ...)`` / ``hasattr(obj, name)``: ``(obj, name expression)``, else ``None``."""
    if isinstance(call.func, ast.Name) and call.func.id in ("getattr", "hasattr") and len(call.args) >= 2:
        return call.args[0], call.args[1]
    return None


def _literal(expr):
    return expr.value if isinstance(expr, ast.Constant) and isinstance(expr.value, str) else None


def _unreal_class(expr, aliases):
    """The ``unreal`` name an expression stands for: ``unreal.X``, ``getattr(unreal, "X", ...)`` or an alias."""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == "unreal":
        return expr.attr
    if isinstance(expr, ast.Name):
        return aliases.get(expr.id)
    found = _getattr_call(expr) if isinstance(expr, ast.Call) else None
    if found and isinstance(found[0], ast.Name) and found[0].id == "unreal":
        return _literal(found[1])
    return None


def _aliases(scope):
    """``name = unreal.X`` (or ``getattr(unreal, "X", None)``) inside ``scope``: ``{name: "X"}``."""
    out = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            cls = _unreal_class(node.value, {})
            if cls:
                out[node.targets[0].id] = cls
    return out


def _base(node):
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.value if not isinstance(node, ast.Call) else node.func
    return node.id if isinstance(node, ast.Name) else None


class _Reader:
    """Records one ``obj.name`` read in the scope of one function (its aliases and loop targets)."""

    def __init__(self, own, out, consts, func, aliases):
        self.own, self.out, self.aliases = own, out, aliases
        self.resolver = Resolver(consts, func)

    def read(self, obj, name):
        if isinstance(obj, ast.Name) and obj.id == "unreal":
            self.out["unreal"].add(name)
        elif _unreal_class(obj, self.aliases):
            self.out["static"].add(f"{_unreal_class(obj, self.aliases)}.{name}")
        elif _base(obj) not in STDLIB | {"self"} and not name.startswith("__") and name not in PYTHON_NAMES | self.own:
            self.out["members"].add(name)

    def read_dynamic(self, obj, expr, where):
        """``getattr(obj, expr)`` with a non-literal name: resolved through loop targets and constants."""
        got = self.resolver.resolve(expr)
        on_unreal = (isinstance(obj, ast.Name) and obj.id == "unreal") or _unreal_class(obj, self.aliases)
        if got is None or any(item[0] != "str" for item in got):
            if on_unreal:
                self.out["dynamic"].add(where)
            return
        for item in got:
            self.read(obj, item[1])


def _names(trees, out):
    own = set()
    for _file, tree, _consts in trees:
        own |= {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        own |= set(module_constants(tree))
    for file, tree, consts in trees:
        funcs = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)]
        owner = {id(n): f for f in funcs for n in ast.walk(f)}
        module_aliases = _aliases(ast.Module(body=[n for n in tree.body if not isinstance(n, ast.FunctionDef)],
                                             type_ignores=[]))
        readers = {}
        for node in ast.walk(tree):
            func = owner.get(id(node))
            if id(func) not in readers:
                aliases = dict(module_aliases, **(_aliases(func) if func is not None else {}))
                readers[id(func)] = _Reader(own, out, consts, func, aliases)
            reader = readers[id(func)]
            if isinstance(node, ast.Attribute):
                reader.read(node.value, node.attr)
            elif isinstance(node, ast.Call) and _getattr_call(node):
                obj, expr = _getattr_call(node)
                if _literal(expr) is not None:
                    reader.read(obj, _literal(expr))
                else:
                    reader.read_dynamic(obj, expr, f"{file}:{node.lineno}")


def extract(scripts_dir=SCRIPTS_DIR):
    trees = []
    for path in sorted(Path(scripts_dir).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        if any(isinstance(n, ast.Import) and any(a.name == "unreal" for a in n.names) for n in tree.body):
            trees.append((path.name, tree, module_constants(tree)))
    out = {key: set() for key in ("unreal", "static", "properties", "members", "dynamic")}
    _properties(trees, _helpers(trees), out)
    _names(trees, out)
    out["unreal"] |= {name.split(".")[0] for name in out["static"]}  # an alias's class is read on the module too
    return {key: sorted(values) for key, values in out.items()}, [name for name, _t, _c in trees]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"refresh {DATA_FILE.relative_to(ROOT)}")
    args = parser.parse_args(argv)
    names, scripts = extract()
    if not args.write:
        json.dump(dict(names, scripts=scripts), sys.stdout, indent=1)
        return 0
    from tools.ue_api import names_file  # imports the tests' stub: only ``--write`` needs it
    left = names_file.update(DATA_FILE, names, names_file.stub_names())
    print(json.dumps({"unverified": left}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
