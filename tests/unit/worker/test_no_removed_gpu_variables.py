# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
AC-19: the four GPU variables replaced by the ``compute`` section (D10) are
used nowhere in ``worker/``, ``deploy/`` or ``tests/`` outside this file.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCANNED = ('worker', 'deploy', 'tests')
SKIPPED_DIRS = {'__pycache__', '.pytest_cache', 'node_modules'}
MAX_BYTES = 2 * 1024 * 1024  # the .blend fixtures are larger; no config lives in them
REMOVED = tuple(f'SETHLANS_{name}'.encode() for name in (
    'FORCE_CPU_ONLY', 'FORCE_GPU_ONLY', 'FORCE_GPU_INDEX', 'GPU_MODE'))


def _files():
    for top in SCANNED:
        for path in (REPO / top).rglob('*'):
            if path.is_file() and not SKIPPED_DIRS.intersection(path.parts) and path.stat().st_size <= MAX_BYTES:
                yield path


def _uses(path):
    data = path.read_bytes()
    return [name.decode() for name in REMOVED if name in data]


def test_the_scan_covers_the_three_trees():
    scanned = {path.relative_to(REPO).parts[0] for path in _files()}
    assert scanned == set(SCANNED)
    assert (REPO / 'deploy' / 'docker' / 'worker-cpu' / 'Dockerfile') in set(_files())


def test_no_removed_gpu_variable_is_used():
    this_file = Path(__file__).resolve()
    found = {str(path.relative_to(REPO)): names for path in _files()
             if path.resolve() != this_file and (names := _uses(path))}
    assert found == {}


def test_the_scan_finds_a_use(tmp_path):
    """The match itself works: a file naming one of the variables is found."""
    sample = tmp_path / 'Dockerfile'
    sample.write_bytes(b'ENV ' + REMOVED[2] + b'=1\n')
    assert _uses(sample) == ['SETHLANS_FORCE_GPU_INDEX']
