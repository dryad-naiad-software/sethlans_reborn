# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Static-analysis regression tests for ``packaging/pyinstaller/manager.spec``.

These tests verify that key data resources are declared in the manager
spec's ``datas`` list and that setup mode and PyMySQL are in its
``hiddenimports``. They do NOT invoke PyInstaller itself; the live
build-time guard lives in the install smoke (issue #197 — the apply
pipeline exercises ``validate_password`` end-to-end, which fails fast if
the password resource is missing).

The wizard counterpart lives at ``tests/unit/wizard/test_pyinstaller_spec``
— this file follows the same text-grep pattern but targets manager-side
resources only.
"""

from __future__ import annotations

import ast
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


# --- Hidden imports (setup mode, PyMySQL) ---------------------------------
#
# The spec is parsed with ``ast`` rather than grepped, so a name that only
# appears in a comment does not count. The live check that these names land
# in PyInstaller's analysis needs PyInstaller itself (a build dependency),
# so it is not part of the unit suite.


def _hidden_import_sources(spec_text: str) -> tuple[set[str], list[ast.Call]]:
    """Return (literal hidden-import names, collect_submodules calls).

    Only statements that assign or extend ``hiddenimports`` count.
    """
    names: set[str] = set()
    calls: list[ast.Call] = []
    for node in ast.walk(ast.parse(spec_text)):
        if isinstance(node, ast.AugAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == "hiddenimports"):
            continue
        if isinstance(value, ast.List):
            names.update(
                elt.value for elt in value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "collect_submodules"
        ):
            calls.append(value)
    return names, calls


def _collected_packages(calls: list[ast.Call]) -> dict[str, ast.Call]:
    return {
        call.args[0].value: call for call in calls
        if call.args and isinstance(call.args[0], ast.Constant)
    }


@pytest.fixture(scope="module")
def hidden_sources(spec_text: str):
    return _hidden_import_sources(spec_text)


@pytest.mark.parametrize("package", ["pymysql", "sethlans_manager", "shared", "django"])
def test_manager_spec_collects_package_submodules(hidden_sources, package: str):
    """PyMySQL and every in-tree / Django submodule are collected."""
    _, calls = hidden_sources
    assert package in _collected_packages(calls), (
        f"manager.spec must add collect_submodules({package!r}) to hiddenimports"
    )


def test_manager_spec_django_collection_is_unfiltered(hidden_sources):
    """A filter on the Django collection could drop django.db.backends.mysql."""
    _, calls = hidden_sources
    call = _collected_packages(calls)["django"]
    assert len(call.args) == 1 and not call.keywords, (
        "collect_submodules('django') must take no filter: the MySQL backend "
        "is loaded by name from DATABASES['default']['ENGINE']"
    )


@pytest.mark.parametrize("module", [
    "pymysql",
    "django.db.backends.mysql",
    "django.db.backends.mysql.base",
    "cryptography.hazmat.primitives.asymmetric.padding",
    "sethlans_manager.settings_base",
    "sethlans_manager.settings_setup",
    "sethlans_manager.mysql_driver",
    "sethlans_manager.db_tls",
    "sethlans_manager.setup_mode",
    "sethlans_manager.setup_mode.entry",
    "sethlans_manager.setup_mode.urls",
    "sethlans_manager.setup_mode.middleware",
    "shared.data_folder",
])
def test_manager_spec_names_hidden_import(hidden_sources, module: str):
    """Modules loaded by name at run time are named explicitly."""
    names, _ = hidden_sources
    assert module in names, f"manager.spec hiddenimports must name {module!r}"


@pytest.mark.parametrize("module", [
    "sethlans_manager.settings_base",
    "sethlans_manager.settings_setup",
    "sethlans_manager.mysql_driver",
    "sethlans_manager.db_tls",
    "sethlans_manager.setup_mode.entry",
    "sethlans_manager.setup_mode.urls",
    "sethlans_manager.setup_mode.middleware",
    "shared.data_folder",
])
def test_manager_spec_hidden_import_exists_in_source(module: str):
    """Every in-tree name in the spec is a real module (no stale names)."""
    root = PROJECT_ROOT if module.startswith("shared.") else PROJECT_ROOT / "manager"
    path = root.joinpath(*module.split("."))
    assert path.with_suffix(".py").is_file() or (path / "__init__.py").is_file(), (
        f"{module!r} is named in manager.spec but has no source file"
    )


def test_manager_requirements_pin_pymysql():
    """The build venv installs manager/requirements.txt; PyMySQL must be in it."""
    reqs = (PROJECT_ROOT / "manager" / "requirements.txt").read_text(encoding="utf-8")
    pins = {line.split("==")[0].strip().lower() for line in reqs.splitlines() if "==" in line}
    assert "pymysql" in pins
