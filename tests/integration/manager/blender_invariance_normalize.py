# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Comparator for the FR-T-2 Blender API snapshot
(``test_renderer_blender_invariance.py``).

A live response is made comparable to the committed snapshot in four
steps:

1. ``strip_new_keys`` drops the key names the Unreal foundation adds
   (FR-MR-10, FR-RT-8), per serializer kind, descending only through the
   nested serializers in ``NESTED``.  Free-form JSON values
   (``render_settings``, ``available_tools``, ...) and error bodies are
   never touched.
2. ``normalize`` replaces every database id with a label built from the
   row's own name (``<job:JobAlpha>``), never from the order in which ids
   appear, so a list returned newest first and the same list returned
   oldest first normalize identically.  DRF timestamps, random media
   file names and the native path separator are replaced too.
3. ``keyed_by_name`` turns a worker-poll list into a mapping keyed by job
   ``name``: the poll's order is the D7 documented exception, so only
   the set of jobs (and each job's body) is compared.
4. ``canonical`` renders the result as sorted JSON text, so ``True``,
   ``1`` and ``1.0`` are all different (Python ``==`` would not tell).
"""

import json
import os
import re

# Response keys the Unreal foundation adds, per serializer kind
# (FR-MR-10; FR-RT-8 for the worker and heartbeat response).
# TiledJob and Animation gain only the nested Job/Asset/Project keys.
NEW_KEYS_BY_KIND = {
    'project': frozenset({'renderer', 'engine_build', 'unreal_cvar_overrides'}),
    'asset': frozenset({
        'source_type', 'archive_path', 'uproject_path', 'archive_sha256',
        'overlay_path', 'overlay_sha256',
    }),
    'job': frozenset({'renderer', 'engine_build', 'routing_note'}),
    'worker': frozenset({'unreal_capabilities', 'gpu_memory'}),
}
NEW_KEY_NAMES = frozenset().union(*NEW_KEYS_BY_KIND.values())

TIMESTAMP_KEYS = frozenset({
    'created_at', 'submitted_at', 'started_at', 'completed_at',
    'added_at', 'last_patch_check', 'last_seen', 'last_heartbeat',
})
# DRF's ``DateTimeField`` output for UTC (``+00:00`` is written as ``Z``).
_DRF_TIMESTAMP = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$')
TIMESTAMP = '<timestamp>'

MEDIA_KEYS = frozenset({'blend_file', 'output_file', 'thumbnail', 'video_file'})

# Keys whose value is the primary key of another row, per object kind.
ID_REFS = {
    'project': {'id': 'project', 'blender_version': 'version'},
    'version': {'id': 'version'},
    # ``project`` is declared explicitly on these serializers, so the
    # ``write_only`` extra_kwarg does not apply and it is returned.
    'asset': {'id': 'asset', 'project': 'project'},
    'job': {
        'id': 'job', 'assigned_worker': 'worker', 'animation': 'animation',
        'tiled_job': 'tiledjob', 'animation_frame': 'frame',
        'blender_version': 'version',
    },
    'tiledjob': {
        'id': 'tiledjob', 'project': 'project', 'blender_version': 'version',
    },
    'animation': {
        'id': 'animation', 'project': 'project', 'blender_version': 'version',
    },
    'frame': {'id': 'frame'},
    'worker': {'id': 'worker'},
}

# Keys whose value is a nested serializer, per object kind.
NESTED = {
    'project': {'blender_version_details': 'version'},
    'asset': {'project_details': 'project'},
    'job': {'asset': 'asset'},
    'tiledjob': {'asset': 'asset', 'project_details': 'project'},
    'animation': {
        'asset': 'asset', 'project_details': 'project', 'frames': 'frame',
    },
}

# Child jobs of a TiledJob / tiled animation get an output pattern built
# with ``os.path.join(root, parent_id, file)``, so the separator is the
# native one.  Any other separator is a behavior change and fails.
_SPAWNED_ROOTS = {'tiled_jobs': 'tiledjob', 'tiled_anim_frames': 'frame'}
_SEP = re.escape(os.sep)
_SPAWNED_PATTERN = re.compile(
    rf'^(?P<root>tiled_jobs|tiled_anim_frames){_SEP}(?P<pk>[^\\/]+){_SEP}(?P<rest>[^\\/]+)$',
)

# ``assets/<project id[:8]>/<uuid4 hex[:8]>.blend`` and similar.
_HEX8_SEGMENT = re.compile(r'(?<=/)[0-9a-f]{8}(?=[/.])')


def canonical(value, indent=None):
    """Return *value* as sorted JSON text (type-exact comparison)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True, indent=indent)


def _children(value):
    if isinstance(value, dict):
        return value.items()
    if isinstance(value, list):
        return ((f'[{index}]', item) for index, item in enumerate(value))
    return ()


def new_key_paths(value, path='$'):
    """Return the path of every ``NEW_KEY_NAMES`` key in *value*, any depth.

    Unlike ``strip_new_keys`` this ignores kinds and also looks inside
    free-form JSON and error bodies: it guards the raw pre-change bodies.
    """
    found = []
    for key, item in _children(value):
        child = f'{path}.{key}' if isinstance(value, dict) else f'{path}{key}'
        if isinstance(value, dict) and key in NEW_KEY_NAMES:
            found.append(child)
        found.extend(new_key_paths(item, child))
    return found


def strip_new_keys(value, kind):
    """Return *value* without the new keys of *kind* and its nested kinds."""
    if kind is None:
        return value
    if isinstance(value, list):
        return [strip_new_keys(item, kind) for item in value]
    if not isinstance(value, dict):
        return value
    drop = NEW_KEYS_BY_KIND.get(kind, frozenset())
    nested = NESTED.get(kind, {})
    return {
        key: strip_new_keys(item, nested.get(key))
        for key, item in value.items() if key not in drop
    }


def build_labels():
    """Map ``(kind, str(pk))`` to a name-based label for every row.

    Raises AssertionError if two rows of one kind would share a label,
    which would make the normalization ambiguous.
    """
    from workers.models import (
        Animation, AnimationFrame, Asset, Job, Project,
        SupportedBlenderVersion, TiledJob, Worker,
    )
    sources = {
        'project': (Project.objects.all(), lambda r: r.name),
        'version': (SupportedBlenderVersion.objects.all(), lambda r: r.series),
        'asset': (Asset.objects.all(), lambda r: r.name),
        'job': (Job.objects.all(), lambda r: r.name),
        'tiledjob': (TiledJob.objects.all(), lambda r: r.name),
        'animation': (Animation.objects.all(), lambda r: r.name),
        'frame': (
            AnimationFrame.objects.select_related('animation'),
            lambda r: f'{r.animation.name}#{r.frame_number}',
        ),
        'worker': (Worker.objects.all(), lambda r: r.hostname),
    }
    labels = {}
    for kind, (rows, name_of) in sources.items():
        names = [(str(row.pk), name_of(row)) for row in rows]
        assert len({name for _, name in names}) == len(names), (
            f'{kind} names must be unique for id normalization: {names}'
        )
        labels.update({(kind, pk): f'<{kind}:{name}>' for pk, name in names})
    return labels


def _label(labels, kind, pk):
    try:
        return labels[(kind, str(pk))]
    except KeyError:
        raise AssertionError(f'No label for {kind} id {pk!r}') from None


def normalize_pattern(value, labels):
    """Normalize a spawned tile's ``output_file_pattern``.

    Patterns outside ``tiled_jobs`` / ``tiled_anim_frames`` are returned
    unchanged.  A spawned pattern must use only ``os.sep``; anything else
    raises AssertionError.
    """
    root = re.split(r'[\\/]', value, maxsplit=1)[0]
    if root not in _SPAWNED_ROOTS:
        return value
    match = _SPAWNED_PATTERN.match(value)
    assert match, f'spawned output pattern does not use os.sep {os.sep!r}: {value!r}'
    parent = _label(labels, _SPAWNED_ROOTS[root], match['pk'])
    return f"{root}/{parent}/{match['rest']}"


def _normalize_field(key, item, kind, labels):
    if item is None:
        return None
    if key in TIMESTAMP_KEYS and isinstance(item, str) and _DRF_TIMESTAMP.match(item):
        return TIMESTAMP
    if key in MEDIA_KEYS and isinstance(item, str):
        return _HEX8_SEGMENT.sub('<hex8>', item)
    if key == 'output_file_pattern' and isinstance(item, str):
        return normalize_pattern(item, labels)
    ref = ID_REFS.get(kind, {}).get(key)
    if ref is not None:
        return _label(labels, ref, item)
    return normalize(item, NESTED.get(kind, {}).get(key), labels)


def normalize(value, kind, labels):
    """Return *value* with ids, timestamps and media names normalized.

    *kind* names the serializer that produced *value* (a key of
    ``ID_REFS``); ``None`` means an error body or a free-form value,
    which is returned unchanged.
    """
    if kind is None:
        return value
    if isinstance(value, list):
        return [normalize(item, kind, labels) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _normalize_field(key, item, kind, labels)
        for key, item in value.items()
    }


def keyed_by_name(jobs):
    """Return a worker-poll job list as ``{name: job}`` (D7 exception)."""
    by_name = {job['name']: job for job in jobs}
    assert len(by_name) == len(jobs), 'poll returned duplicate job names'
    return by_name
