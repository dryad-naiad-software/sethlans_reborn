# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for Qt runtime dependencies in manager Docker images.

Locks in the fix for:

* Issue #110 — the Docker test image (``deploy/docker/manager/Dockerfile.test``)
  failed every pytest run with an ``INTERNALERROR`` because ``pytest-qt`` probes
  ``PySide6.QtCore`` inside ``pytest_configure``, and the image lacked the
  shared libraries PySide6 links against (``libglib-2.0.so.0`` and friends).
  The e2e image (``deploy/docker/manager/Dockerfile.e2e``) was also missing
  ``libglib2.0-0``, ``libfontconfig1``, and ``libdbus-1-3``.

``requirements-dev.txt`` pulls in ``PySide6-Essentials``, which is a Python-only
wheel that links against system ``.so`` files. Without these packages present
in the base image, any test run fails at collection time before a single test
executes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = REPO_ROOT / "deploy" / "docker" / "manager"
DOCKERFILE_TEST = DOCKER_DIR / "Dockerfile.test"
DOCKERFILE_E2E = DOCKER_DIR / "Dockerfile.e2e"

# Packages required by every PySide6-based image. pytest-qt imports
# PySide6.QtCore during pytest_configure; its .so chain links through
# glib, GL, fontconfig, dbus, xkbcommon, and egl.
REQUIRED_PACKAGES = (
    "libdbus-1-3",
    "libegl1",
    "libfontconfig1",
    "libgl1",
    "libglib2.0-0",
    "libxkbcommon0",
)


@pytest.fixture(scope="module")
def dockerfile_test_text() -> str:
    return DOCKERFILE_TEST.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerfile_e2e_text() -> str:
    return DOCKERFILE_E2E.read_text(encoding="utf-8")


def _extract_apt_install_block(text: str) -> str:
    """Return the concatenated contents of every apt-get install block.

    Docker ``RUN`` instructions can span multiple lines with ``\\`` line
    continuations. We flatten continuations so a single ``re.findall`` can
    capture the whole package list regardless of formatting.
    """
    # Collapse line continuations: "\\\n" -> " "
    flattened = re.sub(r"\\\s*\n", " ", text)
    blocks = re.findall(
        r"apt-get\s+install\s+[^\n&;]*",
        flattened,
    )
    if not blocks:
        return ""
    return "\n".join(blocks)


@pytest.mark.parametrize("package", REQUIRED_PACKAGES)
def test_dockerfile_test_has_qt_runtime_package(
    dockerfile_test_text: str, package: str
) -> None:
    """Dockerfile.test must install every Qt runtime library PySide6 needs.

    pytest-qt imports PySide6.QtCore during pytest_configure. Without these
    packages, every pytest run fails with INTERNALERROR: ImportError before
    a single test executes. See issue #110.
    """
    block = _extract_apt_install_block(dockerfile_test_text)
    assert block, "Could not find an apt-get install block in Dockerfile.test"
    # Use a regex with word boundaries so "libgl1" does not match "libglu1-mesa".
    pattern = rf"(?<![\w.-]){re.escape(package)}(?![\w.-])"
    assert re.search(pattern, block), (
        f"Dockerfile.test is missing required Qt runtime package "
        f"{package!r}. pytest-qt will fail at collection time without it "
        f"(issue #110). Found apt-get block:\n{block}"
    )


@pytest.mark.parametrize("package", REQUIRED_PACKAGES)
def test_dockerfile_e2e_has_qt_runtime_package(
    dockerfile_e2e_text: str, package: str
) -> None:
    """Dockerfile.e2e must install every Qt runtime library PySide6 needs.

    The e2e image already had libegl1/libgl1/libxkbcommon0 for Blender but
    was missing libglib2.0-0, libfontconfig1, and libdbus-1-3. See issue #110.
    """
    block = _extract_apt_install_block(dockerfile_e2e_text)
    assert block, "Could not find an apt-get install block in Dockerfile.e2e"
    pattern = rf"(?<![\w.-]){re.escape(package)}(?![\w.-])"
    assert re.search(pattern, block), (
        f"Dockerfile.e2e is missing required Qt runtime package "
        f"{package!r}. pytest-qt will fail at collection time without it "
        f"(issue #110). Found apt-get block:\n{block}"
    )


def test_dockerfile_test_apt_teardown_preserved(dockerfile_test_text: str) -> None:
    """Dockerfile.test must still clean apt caches after installing packages.

    Keeps the image small; a regression here would balloon the final layer.
    """
    assert "apt-get clean" in dockerfile_test_text
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile_test_text


def test_dockerfile_e2e_apt_teardown_preserved(dockerfile_e2e_text: str) -> None:
    """Dockerfile.e2e must still clean apt caches after installing packages."""
    assert "apt-get clean" in dockerfile_e2e_text
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile_e2e_text
