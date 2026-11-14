# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal keys of the worker heartbeat (Unreal foundation FR-RT-7, FR-RT-12,
FR-RT-16, FR-RT-17).

The request keys ``unreal_capabilities``, ``gpu_memory``,
``active_job_ids`` and (registration only) ``worker_boot_id`` are
self-reported and untrusted: each is reduced to a fixed shape here before
anything is stored or acted on.  They are applied only to the caller's
own ``Worker`` row, which ``heartbeat_auth.bind_heartbeat_caller`` bound
(issue #215).  A request without them behaves exactly as before.

Kept apart from ``heartbeat_validators`` so both stay under the 300-line
limit.
"""

import logging
import re

from shared.unreal.builds import parse_build
from shared.unreal.unc import validate_unc_dir

from .heartbeat_validators import _sanitize_cpu_name

logger = logging.getLogger(__name__)

MAX_REASON_LENGTH = 200
MAX_INSTALLS = 16
MAX_SHARE_LENGTH = 1024
INSTALL_SOURCES = frozenset({'launcher', 'config'})
GPU_MEMORY_SOURCES = frozenset({'nvidia-smi', 'override'})
MAX_GPUS = 16
MAX_GPU_INDEX = 63
MAX_GPU_NAME_LENGTH = 128
MAX_TOTAL_MB = 1_048_576
MAX_AGE_S = 86_400
ATTRIBUTION_SKEW_MB = 64
MAX_ACTIVE_JOB_IDS = 256
# Job ids are database integers: a larger value would overflow the query.
MAX_JOB_ID = 2 ** 31 - 1
_BOOT_ID = re.compile(r'[0-9a-f]{32}', re.ASCII)
# Printable ASCII except the characters that matter in HTML and Markdown.
_REASON_UNSAFE = re.compile(r'[^\x20-\x7e]|[<>`]')


def _is_int(value):
    """``int`` but not ``bool`` (``True`` is an ``int`` in Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _int_in(value, low, high):
    return _is_int(value) and low <= value <= high


def _sanitize_reason(raw):
    """Return ``disabled_reason`` as printable ASCII of at most 200 chars.

    Non-strings give ``''``.  Every character outside ``[\\x20-\\x7E]``,
    and ``<``, ``>`` and the backtick, becomes ``?``.  Unlike
    ``_sanitize_cpu_name`` this never blanks the value: a reason such as
    ``invalid SETHLANS_UNREAL_SHARE: component 'a:b' ...`` keeps its text.
    """
    if not isinstance(raw, str):
        return ''
    return _REASON_UNSAFE.sub('?', raw)[:MAX_REASON_LENGTH]


def _valid_install(raw):
    if not isinstance(raw, dict):
        return None
    build, supported, source = raw.get('build'), raw.get('supported'), raw.get('source')
    try:
        parse_build(build)
    except ValueError:
        return None
    if not isinstance(supported, bool) or source not in INSTALL_SOURCES:
        return None
    return {'build': build, 'supported': supported, 'source': source}


def _canonical_share(raw):
    if not isinstance(raw, str) or raw == '' or len(raw) > MAX_SHARE_LENGTH:
        return ''
    try:
        root = validate_unc_dir(raw)
    except ValueError:
        return ''
    return raw if root.canonical == raw else ''


def _validate_unreal_capabilities(raw):
    """Reduce a reported ``unreal_capabilities`` to its FR-RT-7 shape, else ``{}``."""
    if not isinstance(raw, dict) or raw.get('schema') != 1 or not _is_int(raw.get('schema')):
        return {}
    installs = raw.get('installs', [])
    if not isinstance(raw.get('enabled'), bool) or not isinstance(installs, list) \
            or len(installs) > MAX_INSTALLS:
        return {}
    kept = []
    for install in installs:
        valid = _valid_install(install)
        if valid is None:
            logger.debug("Dropping invalid Unreal install entry: %r", repr(install)[:200])
        else:
            kept.append(valid)
    caps = {
        'schema': 1,
        'enabled': raw['enabled'],
        'disabled_reason': _sanitize_reason(raw.get('disabled_reason', '')),
        'installs': kept,
    }
    if 'share' in raw:
        caps['share'] = _canonical_share(raw['share'])
    if 'share_reachable' in raw:
        caps['share_reachable'] = raw['share_reachable'] if isinstance(raw['share_reachable'], bool) else False
    return caps


def _attribution(raw, total):
    sethlans, other = raw.get('sethlans_used_mb'), raw.get('other_used_mb')
    for value in (sethlans, other):
        if value is not None and not _int_in(value, 0, total):
            return None, None
    if sethlans is not None and other is not None and sethlans + other > total + ATTRIBUTION_SKEW_MB:
        return None, None
    return sethlans, other


def _valid_gpu(raw, seen):
    if not isinstance(raw, dict):
        return None
    index, total, used = raw.get('index'), raw.get('total_mb'), raw.get('used_mb')
    if not _int_in(index, 0, MAX_GPU_INDEX) or index in seen:
        return None
    if not _int_in(total, 0, MAX_TOTAL_MB) or not _int_in(used, 0, total):
        return None
    name = raw.get('name')
    name = _sanitize_cpu_name(name) if isinstance(name, str) and len(name) <= MAX_GPU_NAME_LENGTH else ''
    sethlans, other = _attribution(raw, total)
    seen.add(index)
    return {
        'index': index, 'name': name, 'total_mb': total, 'used_mb': used,
        'sethlans_used_mb': sethlans, 'other_used_mb': other,
    }


def _validate_gpu_memory(raw):
    """Reduce a reported ``gpu_memory`` to its FR-RT-16 shape, else ``{}``."""
    if not isinstance(raw, dict) or not _is_int(raw.get('schema')) or raw.get('schema') != 1:
        return {}
    gpus = raw.get('gpus')
    if raw.get('source') not in GPU_MEMORY_SOURCES or not _int_in(raw.get('age_s'), 0, MAX_AGE_S) \
            or not isinstance(gpus, list) or len(gpus) > MAX_GPUS:
        return {}
    kept, seen = [], set()
    for gpu in gpus:
        valid = _valid_gpu(gpu, seen)
        if valid is None:
            logger.debug("Dropping invalid gpu_memory entry: %r", repr(gpu)[:200])
        else:
            kept.append(valid)
    return {'schema': 1, 'source': raw['source'], 'age_s': raw['age_s'], 'gpus': kept}


def _validate_active_job_ids(raw):
    """Return ``active_job_ids`` as a list of ints in 1..2**31-1, or ``None`` if invalid."""
    if not isinstance(raw, list) or len(raw) > MAX_ACTIVE_JOB_IDS:
        return None
    if not all(_int_in(job_id, 1, MAX_JOB_ID) for job_id in raw):
        return None
    return raw


def _validate_boot_id(raw):
    """Return ``worker_boot_id`` if it is 32 lowercase hex chars, else ``None``."""
    if isinstance(raw, str) and _BOOT_ID.fullmatch(raw):
        return raw
    return None


def apply_unreal_registration(worker, data):
    """Set the registration's Unreal fields on ``worker``; return the field names.

    Registration stores ``{}`` for a key that is absent.
    """
    worker.unreal_capabilities = _validate_unreal_capabilities(data.get('unreal_capabilities', {}))
    worker.gpu_memory = _validate_gpu_memory(data.get('gpu_memory', {}))
    return ['unreal_capabilities', 'gpu_memory']


def apply_unreal_heartbeat(worker, data):
    """Update the Unreal fields a heartbeat sent; return the field names.

    A key that is absent leaves the stored value unchanged (the
    ``available_tools`` rule).
    """
    fields = []
    if 'unreal_capabilities' in data:
        worker.unreal_capabilities = _validate_unreal_capabilities(data['unreal_capabilities'])
        fields.append('unreal_capabilities')
    if 'gpu_memory' in data:
        worker.gpu_memory = _validate_gpu_memory(data['gpu_memory'])
        fields.append('gpu_memory')
    return fields
