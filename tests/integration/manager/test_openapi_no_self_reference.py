# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
No OpenAPI component may be composed of itself (issue #282).

A component whose ``oneOf`` / ``anyOf`` / ``allOf`` reaches back to
itself makes openapi-typescript emit a type alias that refers to
itself, which TypeScript rejects with TS2502.  drf-spectacular builds
one when a ``PolymorphicProxySerializer`` shares its ``component_name``
with one of the serializers it wraps (it was ``FFmpegStatus``).

Only composition edges count: a property that refers back to its own
component (a tree) is a legal recursive type in TypeScript.
"""

import pytest
from drf_spectacular.generators import SchemaGenerator

COMPOSITION_KEYS = ("oneOf", "anyOf", "allOf")
REF_PREFIX = "#/components/schemas/"


def _composition_refs(schema):
    """Names the component is directly composed of (top-level keys)."""
    names = set()
    for key in COMPOSITION_KEYS:
        for entry in schema.get(key, []):
            ref = entry.get("$ref", "") if isinstance(entry, dict) else ""
            if ref.startswith(REF_PREFIX):
                names.add(ref[len(REF_PREFIX):])
    return names


def _composition_cycles(components):
    """Return the names of every component on a composition cycle."""
    graph = {
        name: _composition_refs(schema)
        for name, schema in components.items()
    }
    on_cycle = set()
    for start in graph:
        stack, seen = list(graph[start]), set()
        while stack:
            current = stack.pop()
            if current == start:
                on_cycle.add(start)
                break
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph.get(current, ()))
    return on_cycle


@pytest.fixture(scope="module")
def components(django_db_blocker):
    with django_db_blocker.unblock():
        schema = SchemaGenerator().get_schema(request=None, public=True)
    return schema["components"]["schemas"]


class TestCycleDetector:
    """The detector itself finds direct and indirect composition cycles."""

    def test_direct_self_reference_is_found(self):
        comps = {"A": {"oneOf": [{"$ref": REF_PREFIX + "A"}]}}
        assert _composition_cycles(comps) == {"A"}

    def test_indirect_cycle_is_found(self):
        comps = {
            "A": {"allOf": [{"$ref": REF_PREFIX + "B"}]},
            "B": {"anyOf": [{"$ref": REF_PREFIX + "A"}]},
        }
        assert _composition_cycles(comps) == {"A", "B"}

    def test_recursive_property_is_not_a_cycle(self):
        comps = {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": REF_PREFIX + "Node"}},
            },
        }
        assert _composition_cycles(comps) == set()


@pytest.mark.django_db
class TestManagerSchema:

    def test_no_component_is_composed_of_itself(self, components):
        cycles = _composition_cycles(components)
        assert not cycles, (
            "OpenAPI components composed of themselves (TS2502 in the "
            f"generated Angular types): {sorted(cycles)}"
        )

    def test_ffmpeg_status_leaf_is_a_plain_object(self, components):
        slim = components["FFmpegStatus"]
        assert not _composition_refs(slim)
        assert set(slim["properties"]) == {"video_assembly_ready"}
