# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The packager mirrors the worker's archive rules (Unreal foundation
FR-PK-9, FR-PK-13, FR-PK-14). ``shared`` cannot import the worker, so this
test pins the two copies together: a rule change on one side fails here.
"""
import pytest

from sethlans_worker_agent.project_archive import zip_precheck, zip_rules, zip_scan
from shared.project_packager import rules

# Names the packager may see on disk. Backslashes and unencodable names are
# refused by the packager only (the worker never sees them as names).
CORPUS = [
    'Main.umap', 'CON', 'con.txt', 'Con .txt', 'CONIN$', 'conout$', 'COM1', 'COM9.x', 'COM10', 'COM0',
    'LPT1', 'COM¹', 'COM²', 'LPT³.y', 'PRN', 'AUX.', 'NUL', 'NULL', 'a.', 'a ', ' a', 'a b', 'PROGRA~1',
    'x~9', '~x', 'a<b', 'a>b', 'a:b', 'a"b', 'a|b', 'a?b', 'a*b', 'a\x00b', 'a\x01b', 'a\x7fb', 'a\tb',
    'x' * 255, 'x' * 256, 'Hé', 'straße', '日本語.uasset', '.hidden', 'a..b', '...',
]


@pytest.mark.parametrize('component', CORPUS)
def test_component_rules_agree(component):
    try:
        zip_rules.check_component(component)
        worker_accepts = True
    except ValueError:
        worker_accepts = False
    assert (rules.component_problem(component) is None) is worker_accepts


@pytest.mark.parametrize('component', ['a\\b', 'bad\udcffname'])
def test_packager_is_stricter_where_the_worker_would_misread_a_name(component):
    assert rules.component_problem(component) is not None


def test_fold_key_agrees():
    for path in ('Content/Straße/A', 'content/ǅ/x', 'C/İ', 'C/ß'):
        assert rules.fold_key(path) == zip_rules.fold_key(path)


def test_limits_agree():
    assert rules.WORKER_MAX_ENTRIES == zip_precheck.MAX_ENTRIES
    assert rules.WORKER_MAX_CD_BYTES == zip_precheck.MAX_CD_BYTES
    assert rules.ENTRY_RATIO_LIMIT == zip_scan.ENTRY_RATIO_LIMIT
    assert rules.ENTRY_RATIO_MIN_BYTES == zip_scan.ENTRY_RATIO_MIN_BYTES
    assert rules.TOTAL_RATIO_LIMIT == zip_scan.TOTAL_RATIO_LIMIT
    assert rules.MAX_COMPONENT == zip_rules.MAX_COMPONENT
    assert rules.MAX_FILES > rules.WORKER_MAX_ENTRIES  # FR-PK-13: package() enforces the worker limit


def test_rule_data_is_identical():
    assert rules._FORBIDDEN == zip_rules._FORBIDDEN
    assert rules._RESERVED == zip_rules._RESERVED
    assert rules._SHORT_NAME.pattern == zip_rules._SHORT_NAME.pattern


def _worker_accepts(component: str) -> bool:
    try:
        zip_rules.check_component(component)
    except ValueError:
        return False
    return True


def _sweep():
    for code in range(0x3000):
        if 0xD800 <= code <= 0xDFFF:
            continue
        ch = chr(code)
        yield from (ch, 'a' + ch, ch + 'a', 'a' + ch + 'b')
    for stem in sorted(zip_rules._RESERVED):
        for suffix in ('', '.x', ' ', ' .x'):
            yield stem + suffix
            yield stem.lower() + suffix


def test_packager_is_at_least_as_strict_over_a_generated_sweep():
    lenient = [c for c in _sweep() if rules.component_problem(c) is None and not _worker_accepts(c)]
    assert lenient == []  # packager accepts => worker accepts
