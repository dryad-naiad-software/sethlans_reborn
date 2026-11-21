# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Name resolution for ``extract_names.py`` (issue #283): what string a name expression in a UE script holds.

A literal, an f-string or ``"prefix" + name`` of resolvable parts, a module constant (``CONST`` or
``CONST[a:b]``), a ``for`` or comprehension target over such a constant, or a parameter of the enclosing
function (which makes that function a helper that passes the name on).
"""
import ast


def module_constants(tree):
    """Module-level ``NAME = value`` (and ``A, B = x, y``) assignments."""
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value
                elif isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    consts.update((t.id, v) for t, v in zip(target.elts, node.value.elts) if isinstance(t, ast.Name))
    return consts


def _loop_bindings(func):
    """``name -> [(iterable, position, scope)]`` for every ``for`` and comprehension target inside ``func``; the
    scope (the ``for`` statement or the comprehension expression) is where the target holds."""
    out, loops = {}, [(n.target, n.iter, n) for n in ast.walk(func) if isinstance(n, ast.For)]
    comps = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
    loops += [(g.target, g.iter, n) for n in ast.walk(func) if isinstance(n, comps) for g in n.generators]
    for target, iterable, scope in loops:
        if isinstance(target, ast.Name):
            out.setdefault(target.id, []).append((iterable, None, scope))
        elif isinstance(target, ast.Tuple):
            for pos, elt in enumerate(target.elts):
                if isinstance(elt, ast.Name):
                    out.setdefault(elt.id, []).append((iterable, pos, scope))
    return out


def _span(node):
    return (node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset)


def _enclosing(bindings, at):
    """The innermost bindings whose scope holds the use ``at``; all of them when none does (or no position)."""
    if at is None or not hasattr(at, "lineno"):
        return bindings
    inside = [b for b in bindings if _span(b[2])[0] <= _span(at)[0] and _span(at)[1] <= _span(b[2])[1]]
    if not inside:
        return bindings
    innermost = min(inside, key=lambda b: (_span(b[2])[1][0] - _span(b[2])[0][0], _span(b[2])[1][1]))
    return [b for b in inside if b[2] is innermost[2]]


class Resolver:
    """Resolves a name expression to ``{("str", text)}`` or ``{("param", index, prefix)}``; ``None``: unresolved."""

    def __init__(self, consts, func=None):
        self.consts, self.func = consts, func
        self.params = [a.arg for a in func.args.args] if func is not None else []
        self.loops = _loop_bindings(func) if func is not None else {}

    def items(self, expr):
        """The elements of an iterable expression (a literal or a module constant)."""
        if isinstance(expr, ast.Name) and expr.id in self.consts:
            expr = self.consts[expr.id]
        if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Slice):  # CONST[a:b] with literal bounds
            elts, bounds = self.items(expr.value), (expr.slice.lower, expr.slice.upper)
            if elts is None or expr.slice.step or not all(b is None or isinstance(b, ast.Constant) for b in bounds):
                return None
            return elts[slice(*(None if b is None else b.value for b in bounds))]
        return list(expr.elts) if isinstance(expr, (ast.Tuple, ast.List)) else None

    def _name(self, name, seen, at=None):
        if name in self.loops and _enclosing(self.loops[name], at) is not self.loops[name]:
            return self._loop(name, seen, at)  # a loop target inside its loop wins over a parameter of that name
        if name in self.params:
            return {("param", self.params.index(name), "")}
        if name in self.loops:
            return self._loop(name, seen, at)
        if name in self.consts and name not in seen:
            return self.resolve(self.consts[name], seen | {name})
        return None

    def _loop(self, name, seen, at):
        out = set()
        for iterable, pos, _scope in _enclosing(self.loops[name], at):
            elts = self.items(iterable)
            if elts is None:
                return None
            for elt in elts:
                part = elt.elts[pos] if pos is not None and isinstance(elt, ast.Tuple) else elt
                got = self.resolve(part, seen) if pos is None or isinstance(elt, ast.Tuple) else None
                if got is None:
                    return None
                out |= got
        return out

    def resolve(self, expr, seen=frozenset()):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return {("str", expr.value)}
        if isinstance(expr, ast.Name):
            return self._name(expr.id, seen, expr)
        if isinstance(expr, ast.IfExp):  # either branch: both names are used
            body, orelse = self.resolve(expr.body, seen), self.resolve(expr.orelse, seen)
            return None if body is None or orelse is None else body | orelse
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return self._join([expr.left, expr.right], seen)
        if isinstance(expr, ast.JoinedStr):
            return self._join([v.value if isinstance(v, ast.FormattedValue) else v for v in expr.values], seen)
        return None

    def _join(self, parts, seen):
        out = {("str", "")}
        for part in parts:
            got = self.resolve(part, seen)
            if got is None:
                return None
            out = {concat(a, b) for a in out for b in got}
            if any(item is None for item in out):
                return None
        return out


def concat(left, right):
    """``str + str`` or ``str + param`` (a prefix); anything else is unresolvable (``None``)."""
    if left[0] == "str" and right[0] == "str":
        return ("str", left[1] + right[1])
    if left[0] == "str" and right[0] == "param" and not right[2]:
        return ("param", right[1], left[1])
    return None
