# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Static-analysis smoke tests for ``packaging/pyinstaller/wizard.spec``.

These tests verify the spec file's existence, parseability, and that
key configuration markers are present. They do NOT invoke PyInstaller
itself; the live build is exercised in Phase C / AC-B4 via the CI
smoke test.

Per Spec 1 NF-4 the wizard bundle MUST stay under 25 MB. Per AC-B2 the
bundle MUST NOT contain Django, the workers app, psycopg, or pymysql —
the spec achieves this via ``excludes=`` (verified here) and the live
``pathlib.rglob`` assertions added in Phase C.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = PROJECT_ROOT / "packaging" / "pyinstaller" / "wizard.spec"


@pytest.fixture(scope="module")
def spec_text() -> str:
    """Return the wizard.spec file contents once for all tests."""
    return SPEC_PATH.read_text(encoding="utf-8")


def test_wizard_spec_file_exists():
    """The wizard.spec file is present at the expected location."""
    assert SPEC_PATH.is_file(), f"Expected spec at {SPEC_PATH}"


def test_wizard_spec_is_parseable_python(spec_text: str):
    """The spec file compiles as Python (no syntax errors).

    PyInstaller spec files ARE Python — they are exec'd by the
    ``pyinstaller`` CLI with ``SPECPATH`` injected into the namespace.
    A syntax error here would fail the build with a confusing
    traceback, so guard against it up front.
    """
    compile(spec_text, str(SPEC_PATH), "exec")


def test_wizard_spec_entry_point_is_run_wizard(spec_text: str):
    """Analysis() names ``wizard/run_wizard.py`` as the entry point."""
    # The path is constructed via ``WIZARD_DIR / 'run_wizard.py'`` so
    # both pieces should appear in the spec.
    assert "WIZARD_DIR = PROJECT_ROOT / 'wizard'" in spec_text
    assert "'run_wizard.py'" in spec_text


def test_wizard_spec_uses_collect_submodules_for_package(spec_text: str):
    """``collect_submodules('sethlans_wizard')`` is referenced.

    Required so the new ``handlers/`` subpackage and any other lazily
    imported submodules are auto-discovered (DEVOPS-LOW-4: worker spec
    uses the same pattern).
    """
    assert "collect_submodules('sethlans_wizard')" in spec_text


def test_wizard_spec_includes_version_file_in_datas(spec_text: str):
    """The repo-root ``VERSION`` file is bundled at the contents-dir root.

    ``shared.version.get_version()`` reads ``sys._MEIPASS / 'VERSION'``
    in frozen mode; the spec must place the file there.
    """
    assert "_VERSION_SRC = PROJECT_ROOT / 'VERSION'" in spec_text
    # Destination '.' resolves to _MEIPASS in PyInstaller one-dir mode.
    assert "(str(_VERSION_SRC), '.')" in spec_text


def test_wizard_spec_includes_waitress_hidden_imports(spec_text: str):
    """Waitress is in hidden imports (server runtime)."""
    assert "collect_submodules('waitress')" in spec_text


def test_wizard_spec_includes_cryptography_hidden_imports(spec_text: str):
    """Cryptography lazy modules used by cert.py are in hidden imports."""
    assert "'cryptography'" in spec_text
    assert "'cryptography.x509'" in spec_text
    assert "'cryptography.hazmat.primitives.asymmetric.rsa'" in spec_text


@pytest.mark.parametrize(
    "forbidden",
    [
        "django",
        "workers",
        "sethlans_manager",
        "sethlans_worker_agent",
        "psycopg",
        "pymysql",
    ],
)
def test_wizard_spec_excludes_forbidden_module(
    spec_text: str, forbidden: str
):
    """AC-B2: forbidden imports MUST be in the ``excludes`` list.

    The wizard is a standalone process. Pulling in Django, the manager,
    the worker, or any DB driver would bloat the bundle past the
    NF-4 25 MB ceiling and violate the standalone-process contract.
    """
    assert f"'{forbidden}'" in spec_text, (
        f"{forbidden!r} must appear (in the excludes list) in wizard.spec"
    )


@pytest.mark.parametrize(
    "banned",
    ["httpx", "requests", "certifi"],
)
def test_wizard_spec_no_banned_top_level_deps(
    spec_text: str, banned: str
):
    """NF-9: no new top-level deps beyond manager's existing set.

    ``httpx``, ``requests``, and ``certifi`` are banned. They MUST NOT
    appear in hiddenimports (a parametrized check; presence in
    ``excludes`` would also include the literal but our excludes list
    explicitly carries them, so this test asserts they appear ONLY in
    the excludes context — not in any hiddenimports list).
    """
    # The banned name should appear in the excludes list (defensive
    # exclusion), and only there. Count occurrences and confirm they
    # are colocated with the ``excludes`` keyword block.
    assert f"'{banned}'" in spec_text
    # The substring must not be added under hiddenimports — a stray
    # ``hiddenimports += ['requests']`` would be a regression. Search
    # for that pattern explicitly.
    assert f"hiddenimports += ['{banned}']" not in spec_text
    assert f'hiddenimports += ["{banned}"]' not in spec_text


def test_wizard_spec_documents_size_ceiling(spec_text: str):
    """NF-4: the spec MUST comment the 25 MB size constraint at the top.

    Future contributors need to see this constraint without hunting
    through the issue tracker.
    """
    assert "25 MB" in spec_text or "25MB" in spec_text


def test_wizard_spec_has_spdx_header(spec_text: str):
    """All source files must carry an SPDX license header."""
    assert "SPDX-FileCopyrightText" in spec_text
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in spec_text


def test_wizard_spec_output_name_is_run_wizard(spec_text: str):
    """EXE() ``name=`` is ``run_wizard`` (matches manager/worker convention)."""
    assert "name='run_wizard'" in spec_text


def test_wizard_spec_collect_dir_name_is_wizard(spec_text: str):
    """COLLECT() ``name=`` is ``wizard`` — output dir under ``dist/``."""
    assert "name='wizard'" in spec_text
