# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``shared.unreal.share_paths`` (Unreal foundation FR-MU-15, FR-MU-6, FR-T-4).
"""
import pytest

from shared.unreal import share_paths, unc

ROOT = unc.validate_unc_dir(r'\\alpha\mestrella\applications\unreal')


class TestValidateShareRelpath:

    @pytest.mark.parametrize('value, kind, suffix, expected', [
        ('incoming/ACOMAnimationSample.zip', 'file', '.zip', 'incoming/ACOMAnimationSample.zip'),
        (r'incoming\ACOM.ZIP', 'file', '.zip', 'incoming/ACOM.ZIP'),
        ('project-fixes', 'dir', None, 'project-fixes'),
        ('a/b c/d', 'dir', None, 'a/b c/d'),
        ('x.zip', 'file', '.zip', 'x.zip'),
        ('Müller/x.zip', 'file', '.zip', 'Müller/x.zip'),
    ])
    def test_accepts_and_normalizes(self, value, kind, suffix, expected):
        assert share_paths.validate_share_relpath(value, kind=kind, suffix=suffix) == expected

    @pytest.mark.parametrize('value', [
        '', '/incoming/x.zip', r'\incoming\x.zip', 'C:\\x.zip', 'c:x.zip',
        r'\\alpha\other\x.zip', '//host/x.zip', r'\\?\C:\x.zip', r'\\.\pipe\x.zip',
        '../x.zip', 'a/../x.zip', './x.zip', 'a/./x.zip', 'incoming//x.zip', 'incoming/x.zip/',
        'CON/x.zip', 'a/nul.txt/x.zip', 'COM1.zip', 'lpt9/x.zip', 'COM\u00b9/x.zip', 'conin$/x.zip',
        'incoming/x.txt', 'incoming /x.zip', 'incoming/ x.zip', 'a./x.zip', 'PROGRA~1/x.zip',
        'a\u202eb/x.zip', 'a\u200bb/x.zip', 'e\u0301/x.zip', 'a:b/x.zip', 'a*/x.zip',
        'a\x00b/x.zip', 'a\x7fb/x.zip', 'x' * 513, '/'.join(['a'] * 21) + '.zip', ('a' * 256) + '/x.zip',
        '.zip',
    ])
    def test_rejects_file(self, value):
        with pytest.raises(ValueError):
            share_paths.validate_share_relpath(value, kind='file', suffix='.zip')

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            share_paths.validate_share_relpath(None, kind='dir')

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            share_paths.validate_share_relpath('a', kind='link')

    def test_messages_name_the_rule(self):
        with pytest.raises(ValueError, match=r'rule 3'):
            share_paths.validate_share_relpath('../x.zip', kind='file', suffix='.zip')
        with pytest.raises(ValueError, match=r'rule 5'):
            share_paths.validate_share_relpath('x.txt', kind='file', suffix='.zip')


class TestResolveInShare:

    def test_joins_under_the_root(self):
        result = share_paths.resolve_in_share(ROOT, 'incoming/x.zip')
        assert result.canonical == r'\\alpha\mestrella\applications\unreal\incoming\x.zip'
        assert result.is_within(ROOT)

    def test_accepts_a_string_root_and_a_bare_share(self):
        result = share_paths.resolve_in_share(r'\\NAS\sethlans\\'[:-1], 'x.zip')
        assert result.canonical == r'\\nas\sethlans\x.zip'

    def test_revalidates_the_relpath(self):
        with pytest.raises(ValueError):
            share_paths.resolve_in_share(ROOT, '../escape.zip')

    def test_resolved_length_is_capped(self):
        long_root = unc.validate_unc_dir('\\\\h\\s\\' + '\\'.join(['d' * 200] * 4))
        with pytest.raises(ValueError, match='rule 1'):
            share_paths.resolve_in_share(long_root, 'e' * 200 + '/' + 'f' * 200 + '/x.zip')

    def test_result_outside_root_is_refused(self, monkeypatch):
        """Defense in depth: forced through a patched validator."""
        other = unc.validate_unc_path(r'\\evil\share\x.zip')
        monkeypatch.setattr(share_paths, 'validate_unc_path', lambda _path: other)
        with pytest.raises(ValueError, match='outside the Sethlans share'):
            share_paths.resolve_in_share(ROOT, 'x.zip')


class TestUncRootRules:
    """FR-T-4: a bare root is valid while a resolved path needs a component."""

    def test_bare_root(self):
        assert unc.validate_unc_dir(r'\\nas\sethlans').rest == ()
        with pytest.raises(ValueError):
            unc.validate_unc_path(r'\\nas\sethlans')

    @pytest.mark.parametrize('a, b, expected', [
        (r'\\alpha\Share\Dir', r'\\ALPHA\share\dir', True),
        (r'\\alpha\share\dir' + '\\', r'\\alpha\share\dir', True),
        (r'\\alpha\share', r'\\alpha.lan\share', False),
        ('', r'\\alpha\share', False),
        (r'\\alpha\share', 'C:\\share', False),
    ])
    def test_shares_match(self, a, b, expected):
        assert unc.shares_match(a, b) is expected

    @pytest.mark.parametrize('path, expected', [
        (r'\\nas\s', True), (r'\\nas.lan\s', False), (r'\\10.0.0.5\s', False),
    ])
    def test_is_single_label_host(self, path, expected):
        assert unc.is_single_label_host(unc.validate_unc_dir(path)) is expected
