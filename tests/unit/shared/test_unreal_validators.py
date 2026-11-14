# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``shared.unreal.validation`` (Unreal foundation FR-MU-5, FR-MU-7, FR-T-4, AC-43).
"""
import pytest

from shared.unreal import validation as v


class TestObjectPath:

    @pytest.mark.parametrize('value', [
        '/Game/Maps/Main', '/Game/Maps/Main.Main', '/Game/Cine-1/Seq_A.Seq_A',
    ])
    def test_accepts(self, value):
        assert v.validate_object_path(value) == value

    @pytest.mark.parametrize('value', [
        'Game/Maps/Main', '/Game', '/Game/Maps/Main.', '/Game/Ma ps/Main', '/Game/Maps/Main\n',
        '/Game/Maps/Main.A.B', '/Game/Maps/"x"', None, '/G/' + 'a' * 520,
    ])
    def test_rejects(self, value):
        with pytest.raises(ValueError):
            v.validate_object_path(value)


class TestCvars:

    def test_normalizes_values_to_str(self):
        assert v.validate_cvars({'r.Streaming.PoolSize': 3000, 'gc.Foo': '4'}) == {
            'r.Streaming.PoolSize': '3000', 'gc.Foo': '4',
        }

    @pytest.mark.parametrize('name', [
        'r.x_abslog', 'x.zendatapath', 'a.cvarsini', 'a.b_logfilename', 'execcmds.x',
        'r.ExecCmds', 'a.ABSLOG', 'a.b_DPCVars', 'nodot', '1r.x', 'r.', 'r..x', 'r.x-y',
        'r.' + 'x' * 130, 'r.x_log', 'r.X_LOG', 'a.b_game', 'r.x_ini', 'r.x_unattended', 'r.x_ddc',
    ])
    def test_rejects_names(self, name):
        with pytest.raises(ValueError):
            v.validate_cvars({name: '1'})

    @pytest.mark.parametrize('name', ['r.xabslog', 'r.Streaming.PoolSize', 'r.xlog', 'r.log_x', 'r.Catalog'])
    def test_accepts_names(self, name):
        """A letter or digit before the switch is not a boundary; a prefix is harmless."""
        assert v.validate_cvars({name: '1'}) == {name: '1'}

    @pytest.mark.parametrize('value', ['', 'a b', 'x' * 65, True, 1.5, None, '1;2', '"1"'])
    def test_rejects_values(self, value):
        with pytest.raises(ValueError):
            v.validate_cvars({'r.Foo': value})

    def test_limits_and_shape(self):
        with pytest.raises(ValueError):
            v.validate_cvars([('r.Foo', '1')])
        with pytest.raises(ValueError):
            v.validate_cvars({f'r.Foo{i}': '1' for i in range(65)})
        assert len(v.validate_cvars({f'r.Foo{i}': '1' for i in range(64)})) == 64

    def test_case_insensitive_duplicates_are_rejected(self):
        with pytest.raises(ValueError, match='twice'):
            v.validate_cvars({'r.Foo': '1', 'R.FOO': '2'})

    def test_suffix_rule_covers_the_whole_denylist(self):
        for switch in v.UE_SWITCH_DENYLIST:
            with pytest.raises(ValueError):
                v.validate_cvars({f'r.x_{switch}': '1'})


class TestUprojectRelpath:

    @pytest.mark.parametrize('value', [
        'ACOMAnimationSample/ACOMAnimationSample.uproject', 'A.uproject', 'a b/c.d/X_1.uproject',
    ])
    def test_accepts(self, value):
        assert v.validate_uproject_relpath(value) == value

    @pytest.mark.parametrize('value', [
        '', '/A/A.uproject', r'A\A.uproject', 'a/b/c/A.uproject', 'A/a.txt', '../A.uproject',
        'a./A.uproject', 'a /A.uproject', '1A.uproject', 'A/' + 'B' * 70 + '.uproject',
        ('a' * 128 + '/') * 2 + 'A.uproject', None,
    ])
    def test_rejects(self, value):
        with pytest.raises(ValueError):
            v.validate_uproject_relpath(value)


class TestSha256:

    def test_strips_and_lowercases(self):
        assert v.validate_sha256('  ' + 'AB' * 32 + '\n') == 'ab' * 32

    @pytest.mark.parametrize('value', ['a' * 63, 'g' * 64, 'a' * 65, None, ''])
    def test_rejects(self, value):
        with pytest.raises(ValueError):
            v.validate_sha256(value)
