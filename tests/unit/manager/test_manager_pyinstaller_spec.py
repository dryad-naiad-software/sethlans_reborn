# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Static-analysis regression tests for ``packaging/pyinstaller/manager.spec``.

These tests verify that key data resources are declared in the manager
spec's ``datas`` list. They do NOT invoke PyInstaller itself; the live
build-time guard lives in the install smoke (issue #197 — the apply
pipeline exercises ``validate_password`` end-to-end, which fails fast if
the password resource is missing).

The wizard counterpart lives at ``tests/unit/wizard/test_pyinstaller_spec``
— this file follows the same text-grep pattern but targets manager-side
resources only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = PROJECT_ROOT / "packaging" / "pyinstaller" / "manager.spec"


@pytest.fixture(scope="module")
def spec_text() -> str:
    """Return the manager.spec file contents once for all tests."""
    return SPEC_PATH.read_text(encoding="utf-8")


def test_manager_spec_file_exists():
    """The manager.spec file is present at the expected location."""
    assert SPEC_PATH.is_file(), f"Expected spec at {SPEC_PATH}"


def test_manager_spec_is_parseable_python(spec_text: str):
    """The spec file compiles as Python (no syntax errors).

    PyInstaller spec files ARE Python — they are exec'd by the
    ``pyinstaller`` CLI with ``SPECPATH`` injected into the namespace.
    A syntax error here would fail the build with a confusing
    traceback, so guard against it up front.
    """
    compile(spec_text, str(SPEC_PATH), "exec")


def test_manager_spec_has_spdx_header(spec_text: str):
    """All source files must carry an SPDX license header."""
    assert "SPDX-FileCopyrightText" in spec_text
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in spec_text


def test_manager_spec_bundles_django_common_passwords_resource(
    spec_text: str,
):
    """Issue #196: ``django/contrib/auth/common-passwords.txt.gz`` MUST
    be declared in ``datas=``.

    PyInstaller's static walker copies ``.py`` files inside packages but
    not arbitrary data resources, so without an explicit ``datas`` entry
    the file is silently dropped from the bundle. At runtime
    ``CommonPasswordValidator`` resolves the resource via
    ``importlib.resources``; if it is missing, ``validate_password()``
    raises ``FileNotFoundError`` inside the apply pipeline's
    ``transaction.atomic()`` block and the launcher surfaces it as
    ``apply operational failure: atomic apply failed: FileNotFoundError``.

    Mirrors the wizard-side regression for issue #190 at
    ``tests/unit/wizard/test_pyinstaller_spec``.
    """
    # The resource filename must appear somewhere in the spec text
    # (defensive check — gives a clearer assertion than the window walk
    # below if the entry is removed entirely).
    assert "'common-passwords.txt.gz'" in spec_text, (
        "manager.spec must reference 'common-passwords.txt.gz' in "
        "datas= (issue #196)"
    )

    # The filename should appear inside a ``collect_data_files(...)``
    # call targeting ``django.contrib.auth`` — not just in a stray
    # comment. Walk lines and confirm at least one window contains
    # both anchors together. ``datas += collect_data_files(...)`` is
    # the project's idiomatic form (see existing migration / locale
    # declarations), and using ``includes=['common-passwords.txt.gz']``
    # surgically scopes the collection to just that file.
    lines = spec_text.splitlines()
    found = False
    for idx, line in enumerate(lines):
        if "collect_data_files" not in line:
            continue
        # Consider this call and the next several lines (covers
        # multi-line argument lists).
        window = "\n".join(lines[idx:idx + 6])
        if (
            "'django.contrib.auth'" in window
            and "'common-passwords.txt.gz'" in window
        ):
            found = True
            break
    assert found, (
        "manager.spec references 'common-passwords.txt.gz' but not "
        "inside a collect_data_files('django.contrib.auth', ...) call "
        "— the file would not be bundled. Confirm the datas entry is "
        "present and correct (issue #196)."
    )
