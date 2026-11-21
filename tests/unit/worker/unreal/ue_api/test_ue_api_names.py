# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Every Unreal Python name the worker's UE-side scripts use, and every name the unit tests' ``unreal`` stub exposes,
is on the verified list ``ue_api_names.json`` (issue #283). The list is generated (``tools/ue_api``), and each
entry carries evidence from UE 5.8.4's source or a real UE run. A wrong name (a C++ name where Python uses a
``ScriptName``, a class UE does not have) fails here instead of falling back silently inside Unreal.
"""
import pytest

from tests.unit.worker.unreal.ue_api._stub_names import stub_names
from tools.ue_api import extract_names, names_file

DATA = names_file.load(extract_names.DATA_FILE)
USED, SCRIPTS = extract_names.extract()
STUBS = stub_names()


def test_every_ue_script_is_scanned():
    expected = sorted(p.name for p in extract_names.SCRIPTS_DIR.glob('*.py') if 'import unreal' in p.read_text('utf-8'))
    assert SCRIPTS == expected and len(SCRIPTS) == 8


def test_the_scripts_resolve_every_name():
    """A name built at run time that the extractor cannot follow would escape the list."""
    assert USED['dynamic'] == []


@pytest.mark.parametrize('category', names_file.CATEGORIES)
def test_the_scripts_use_only_listed_names(category):
    missing = set(USED[category]) - names_file.names(DATA, category)
    assert not missing, f"not on the verified list (run python -m tools.ue_api.extract_names --write): {missing}"


@pytest.mark.parametrize('category', names_file.CATEGORIES)
def test_the_stubs_expose_only_listed_names(category):
    missing = set(STUBS[category]) - names_file.names(DATA, category)
    assert not missing, f"the stub exposes names not on the verified list: {missing}"


@pytest.mark.parametrize('category', names_file.CATEGORIES)
def test_the_list_holds_only_names_in_use(category):
    assert names_file.names(DATA, category) == set(USED[category]) | set(STUBS[category])


VERIFY_HOWTO = ("not yet confirmed by a real UE 5.8.4 run: on a machine with UE, run the PythonScript commandlet "
                "with -script=tools/ue_api/verify_in_unreal.py (see its docstring for SETHLANS_UE_API_PARAMS), then "
                "python -m tools.ue_api.names_file --merge-ue-report <report.json>")


def test_every_listed_name_is_verified_in_real_unreal():
    """Source evidence alone let #283's wrong names through: every name needs a real UE run's confirmation."""
    left = names_file.unverified(DATA)
    assert left == [], f"{VERIFY_HOWTO}: {left}"


@pytest.mark.parametrize('category,name', [
    ('unreal', 'MovieGraphBlueprintLibrary'),  # C++ name; Python's is MovieGraphLibrary (#283)
    ('unreal', 'MoviePipelineBlueprintLibrary'),  # C++ name; Python's is MoviePipelineLibrary
    ('unreal', 'MovieGraphConsoleVariableNode'),  # no such class in UE 5.8.4
    ('static', 'LevelSequenceEditorBlueprintLibrary.set_current_time'),  # deprecated; never a silent fallback
    ('properties', 'override_output_frame_step'),  # the 5.8.4 graph node has no frame step (U-1)
    ('properties', 'cvars'),  # ScriptNoExport in 5.8.4: outside the Python API; get_console_variables() is the API
])
def test_known_wrong_names_are_not_used(category, name):
    assert name not in names_file.names(DATA, category) and name not in USED[category] + STUBS[category]
