# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
AC-19: the four GPU variables replaced by the ``compute`` section (D10) are
used nowhere in ``worker/``, ``deploy/`` or ``tests/`` outside this file.

``worker/`` and ``tests/`` always exist. ``deploy/`` is scanned whenever it
exists; the Docker test image does not copy it (#263).
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
REQUIRED = ('worker', 'tests')
OPTIONAL = ('deploy',)
WORKER_DOCKERFILE = ('deploy', 'docker', 'worker-cpu', 'Dockerfile')
SKIPPED_DIRS = {'__pycache__', '.pytest_cache', 'node_modules'}
MAX_BYTES = 2 * 1024 * 1024  # the .blend fixtures are larger; no config lives in them
REMOVED = tuple(f'SETHLANS_{name}'.encode() for name in (
    'FORCE_CPU_ONLY', 'FORCE_GPU_ONLY', 'FORCE_GPU_INDEX', 'GPU_MODE'))


def _trees(root):
    """The trees to scan under ``root``: the required ones, and each optional one that exists."""
    return REQUIRED + tuple(top for top in OPTIONAL if (root / top).is_dir())


def _files(root=REPO):
    for top in _trees(root):
        for path in (root / top).rglob('*'):
            if path.is_file() and not SKIPPED_DIRS.intersection(path.parts) and path.stat().st_size <= MAX_BYTES:
                yield path


def _uses(path):
    data = path.read_bytes()
    return [name.decode() for name in REMOVED if name in data]


def _check_coverage(root):
    """Every required tree, and ``deploy/`` with its worker Dockerfile when it exists, is scanned."""
    files = set(_files(root))
    scanned = {path.relative_to(root).parts[0] for path in files}
    assert scanned == set(_trees(root))
    assert set(REQUIRED) <= scanned
    if (root / 'deploy').is_dir():
        assert root.joinpath(*WORKER_DOCKERFILE) in files


def _tree(root, with_deploy):
    for top in REQUIRED:
        (root / top).mkdir()
        (root / top / 'a.py').write_text('x = 1\n')
    if with_deploy:
        root.joinpath(*WORKER_DOCKERFILE[:-1]).mkdir(parents=True)
        root.joinpath(*WORKER_DOCKERFILE).write_text('ENV SETHLANS_COMPUTE_METHOD=cpu\n')
    return root


def test_the_scan_covers_the_trees_of_this_checkout():
    _check_coverage(REPO)


@pytest.mark.parametrize('with_deploy', [True, False])
def test_coverage_check_with_and_without_deploy(tmp_path, with_deploy):
    root = _tree(tmp_path, with_deploy)
    _check_coverage(root)
    assert ('deploy' in _trees(root)) is with_deploy


def test_coverage_check_fails_when_deploy_lacks_the_worker_dockerfile(tmp_path):
    root = _tree(tmp_path, with_deploy=True)
    root.joinpath(*WORKER_DOCKERFILE).unlink()
    (root / 'deploy' / 'README').write_text('deploy notes\n')
    with pytest.raises(AssertionError):
        _check_coverage(root)


def test_coverage_check_fails_without_a_required_tree(tmp_path):
    root = _tree(tmp_path, with_deploy=True)
    (root / 'worker' / 'a.py').unlink()
    (root / 'worker').rmdir()
    with pytest.raises(AssertionError):
        _check_coverage(root)


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
