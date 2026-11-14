# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``shared.unreal.builds`` and ``shared.unreal.unc`` (Unreal foundation
FR-MU-5, FR-MU-6). The worker needs these for detection and its share
(chunk 4); the manager-side tables of FR-T-4 extend them.
"""
import pytest

from shared.unreal import builds, unc


class TestBuilds:

    def test_parse(self):
        parsed = builds.parse_build('5.8.4-58902052')
        assert (parsed.major, parsed.minor, parsed.patch, parsed.changelist) == (5, 8, 4, 58902052)
        assert str(parsed) == '5.8.4-58902052'

    @pytest.mark.parametrize('value', ['5.8.4', '5.8.4-0', '5.8-1', 'v5.8.4-1', '5.8.4-1 ', 5,
                                       '1234.1.1-1', '5.8.4-1234567890123', '٥.8.4-1',
                                       '5.8.4-58902052\n', '05.8.4-1', '5.08.4-1', '5.8.04-1',
                                       '5.8.4-058902052'])
    def test_rejects(self, value):
        with pytest.raises(ValueError):
            builds.parse_build(value)

    @pytest.mark.parametrize('value,expected', [
        ('5.8.4-58902052', True), ('5.8.0-1', True), ('5.7.4-51494982', False),
        ('5.9.0-1', False), ('5.8.4-0', False), ('junk', False), (None, False),
    ])
    def test_is_supported(self, value, expected):
        assert builds.is_supported(value) is expected


def test_build_re_rejects_a_trailing_newline():
    import re
    assert re.match(builds.BUILD_RE, '5.8.4-58902052')
    assert not re.match(builds.BUILD_RE, '5.8.4-58902052\n')


class TestValidateUncDir:

    @pytest.mark.parametrize('raw,canonical', [
        (r'\\nas\sethlans', r'\\nas\sethlans'),
        ('\\\\NAS.Lan\\Sethlans\\', r'\\nas.lan\Sethlans'),
        (r'\\alpha\mestrella\applications\unreal', r'\\alpha\mestrella\applications\unreal'),
        (r'\\10.0.0.5\Render Farm\Projects', r'\\10.0.0.5\Render Farm\Projects'),
    ])
    def test_accepts_and_canonicalizes(self, raw, canonical):
        assert unc.validate_unc_dir(raw).canonical == canonical

    @pytest.mark.parametrize('raw,rule', [
        (r'\\a', 'rule 1'), ('\\\\nas\\s\x01', 'rule 1'), ('//nas/share', 'rule 1'),
        (r'\\nas\share' + 'x' * 1100, 'rule 1'),
        (r'\\?\C:\share', 'rule 2'), (r'\\.\pipe\x', 'rule 2'), (r'\\\nas\share', 'rule 2'),
        (r'\\-nas\share', 'rule 2'), (r'\\nas', 'rule 3'), (r'\\nas\share\\x', 'rule 3'),
        (r'\\nas\share\a:b', 'rule 4'), (r'\\nas\share\..', 'rule 4'), (r'\\nas\share\x.', 'rule 4'),
        ('\\\\nas\\share\\x ', 'rule 4'), ('\\\\nas\\share\\ x', 'rule 4'), (r'\\nas\PROGRA~1', 'rule 4'),
        ('\\\\nas\\share\\a\u202eb', 'rule 4'), ('\\\\nas\\share\\a\u200bb', 'rule 4'),
        ('\\\\nas\\share\\e\u0301', 'rule 4'),
        (r'\\nas\C$', 'rule 5'), (r'\\nas\ADMIN$', 'rule 5'), (r'\\nas\ipc$', 'rule 5'),
        (r'\\nas\pipe', 'rule 5'), (r'\\nas\MailSlot', 'rule 5'),
        (r'C:\share', 'rule 7'), ('/srv/share', 'rule 1'),
    ])
    def test_rejects_naming_the_rule(self, raw, rule):
        with pytest.raises(ValueError, match=rule):
            unc.validate_unc_dir(raw)

    def test_resolved_path_needs_a_component(self):
        with pytest.raises(ValueError, match='rule 3'):
            unc.validate_unc_path(r'\\nas\share')
        assert unc.validate_unc_path(r'\\nas\share\x.zip').rest == ('x.zip',)


class TestComparisons:

    @pytest.mark.parametrize('a,b,expected', [
        (r'\\nas\Sethlans', '\\\\NAS\\sethlans\\', True),
        (r'\\nas\s\Projects', r'\\nas\S\projects', True),
        (r'\\alpha\s', r'\\alpha.lan\s', False),
        (r'\\nas\s', r'\\nas\s\x', False),
        ('', r'\\nas\s', False), (r'\\nas\s', '', False),
        (r'\\nas\C$', r'\\nas\C$', False),
        ('\\\\nas\\Straße', '\\\\nas\\STRASSE', False),   # NTFS: ß is not ss
        ('\\\\nas\\Straße', '\\\\nas\\STRAßE', True),
        ('\\\\nas\\Ärger', '\\\\nas\\ärger', True),
    ])
    def test_shares_match(self, a, b, expected):
        assert unc.shares_match(a, b) is expected

    def test_fold_is_length_preserving(self):
        for text in ('Straße', 'ﬁle', 'İstanbul', 'ǅ'):
            assert len(unc.fold_component(text)) == len(text)
        assert unc.fold_component('abc') == 'ABC'

    def test_is_within_does_not_equate_sharp_s(self):
        root = unc.validate_unc_dir('\\\\nas\\share\\Straße')
        assert not unc.validate_unc_path('\\\\nas\\share\\STRASSE\\a.zip').is_within(root)
        assert unc.validate_unc_path('\\\\nas\\share\\strasse\\a.zip').is_within(
            unc.validate_unc_dir('\\\\nas\\share\\Strasse'))

    def test_is_within_whole_components(self):
        root = unc.validate_unc_dir(r'\\nas\share\proj')
        assert unc.validate_unc_path(r'\\NAS\Share\Proj\a.zip').is_within(root)
        assert not unc.validate_unc_path(r'\\nas\share\projects\a.zip').is_within(root)

    @pytest.mark.parametrize('raw,expected', [
        (r'\\alpha\s', True), (r'\\alpha.lan\s', False), (r'\\10.0.0.5\s', False), (r'\\3232235777\s', False),
    ])
    def test_single_label(self, raw, expected):
        assert unc.is_single_label_host(unc.validate_unc_dir(raw)) is expected
