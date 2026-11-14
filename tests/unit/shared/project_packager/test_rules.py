# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Packager allowlist and name rules (Unreal foundation FR-PK-2, FR-PK-3,
FR-PK-4, FR-PK-9).
"""
import pytest

from shared.project_packager import (
    EXTENSION_DIRS, ExtensionRule, PackagerError, PackagerOptions, UnsupportedProjectFeature, rules,
)


@pytest.mark.parametrize('name', ['.hidden', '.git', '.DS_Store', 'Thumbs.db', 'thumbs.DB', 'desktop.ini',
                                  'DESKTOP.INI'])
def test_dotfiles_and_os_junk_are_skipped(name):
    assert rules.is_skipped_child(name) is True


@pytest.mark.parametrize('name', ['Main.umap', 'Thumbs.db.bak', 'my.desktop.ini', 'x.'])
def test_ordinary_names_are_kept(name):
    assert rules.is_skipped_child(name) is False


@pytest.mark.parametrize('component, reason', [
    ('CON', 'reserved'), ('con.uasset', 'reserved'), ('CONIN$', 'reserved'), ('conout$.txt', 'reserved'),
    ('COM1', 'reserved'), ('LPT9.x', 'reserved'), ('COM¹', 'reserved'), ('lpt³.txt', 'reserved'),
    ('NUL .txt', 'reserved'),
    ('a.', 'space or a dot'), ('a ', 'space or a dot'),
    ('PROGRA~1', 'short-name'), ('x~0y', 'short-name'),
    ('a:b', '<>:"|?*'), ('a?b', '<>:"|?*'), ('a"b', '<>:"|?*'),
    ('a\x00b', 'control'), ('a\x1fb', 'control'), ('a\x7fb', 'control'),
    ('a\\b', 'backslash'),
    ('x' * 256, 'longer than 255'),
    ('bad\udcffname', 'UTF-8'),
    ('', 'empty'), ('..', 'empty'),
])
def test_component_problems(component, reason):
    problem = rules.component_problem(component)
    assert problem is not None and reason in problem


@pytest.mark.parametrize('component', ['Main.umap', 'CONSOLE.uasset', 'COM10', 'COM0', 'x' * 255, 'Hé.uasset',
                                       '~tilde', 'a~b', 'ab~ c'])
def test_component_accepted(component):
    assert rules.component_problem(component) is None


def test_name_problems_report_the_first_bad_component_per_path():
    problems = rules.name_problems(['Content/ok.uasset', 'Content/CON/x.uasset', 'Content/a~1/b.'])
    assert problems == [('Content/CON/x.uasset', 'Windows reserved device name'),
                        ('Content/a~1/b.', '8.3 short-name shape (~ followed by a digit)')]


def test_collisions_ignore_letter_case_per_character():
    assert rules.collisions(['Content/A', 'Content/a', 'Content/B/x', 'Content/b/X']) == [
        ('Content/a', 'same name as Content/A when letter case is ignored'),
        ('Content/b/X', 'same name as Content/B/x when letter case is ignored'),
    ]
    assert rules.collisions(['Content/straße', 'Content/strasse']) == []  # NTFS-like per-character fold


@pytest.mark.parametrize('uproject, stem', [('Demo.uproject', 'Demo'), ('A' * 64 + '.uproject', 'A' * 64),
                                            ('x_1.uproject', 'x_1')])
def test_valid_project_names(uproject, stem):
    assert rules.validate_project_name(uproject) == stem


@pytest.mark.parametrize('uproject', ['1x.uproject', '_x.uproject', 'a-b.uproject', 'A' * 65 + '.uproject',
                                      'Dé.uproject', 'Demo.UPROJECT', '.uproject'])
def test_invalid_project_names(uproject):
    with pytest.raises(PackagerError, match='project name'):
        rules.validate_project_name(uproject)


@pytest.mark.parametrize('name', ['Plugins', 'Source', 'Saved', 'Anything'])
def test_extension_registry_is_empty_in_phase_1(name):
    assert EXTENSION_DIRS == {}
    with pytest.raises(UnsupportedProjectFeature) as info:
        rules.accepted_extensions((name,))
    assert str(info.value) == f"'{name}/' packaging is not supported yet (parked decision)"


def test_a_registered_extension_is_accepted(monkeypatch):
    monkeypatch.setitem(EXTENSION_DIRS, 'Movies', ExtensionRule(description='test'))
    assert rules.accepted_extensions(('Movies',)) == ('Movies',)


def test_extra_top_level_must_be_names():
    with pytest.raises(PackagerError, match='tuple of directory names'):
        rules.accepted_extensions('Plugins')


@pytest.mark.parametrize('size, csize, limit, minimum, expected', [
    (201, 1, 200, 0, True), (200, 1, 200, 0, False), (0, 0, 200, 0, False),
    (2 * rules.MIB, 10, 200, rules.MIB, True), (rules.MIB - 1, 1, 200, rules.MIB, False),
])
def test_ratio_test_matches_the_worker_formula(size, csize, limit, minimum, expected):
    assert rules.ratio_exceeds(size, csize, limit, minimum) is expected


def test_options_validate():
    with pytest.raises(PackagerError, match='on_unsupported'):
        PackagerOptions(on_unsupported='ignore')
    for bad in (0, -1, True, 1.5):
        with pytest.raises(PackagerError, match='max_total_bytes'):
            PackagerOptions(max_total_bytes=bad)
    assert PackagerOptions().max_total_bytes == 512 * 1024 ** 3
