# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``config_unreal`` parsing (Unreal foundation FR-WC-1, FR-WC-2, NF-6;
FR-T-5 "config_unreal").
"""
import pytest

from sethlans_worker_agent import config_unreal


def getter(values):
    """A ``get_config_value`` stand-in that records the keys it was asked for."""
    asked = []

    def get_value(section, key, default):
        assert section == 'unreal'
        asked.append(key)
        return values.get(key, default)

    get_value.asked = asked
    return get_value


class TestDefaults:

    def test_defaults(self):
        values = config_unreal.load(getter({}))
        assert values['ENABLED'] is False
        assert values['SHARE'] == ''
        assert values['ENGINE_PATHS'] == ''
        assert values['CACHE_DIR'] == config_unreal.default_cache_dir()
        assert values['WORKCOPY_MODE'] == 'hardlink'
        assert values['SHARE_RECHECK_SECONDS'] == 60
        assert values['SHARE_RECHECK_MAX_SECONDS'] == 900
        assert values['VRAM_SAMPLE_SECONDS'] == 30
        assert values['RESCAN_SECONDS'] == 600
        assert values['VRAM_MB'] == 0
        assert values['HANG_SECONDS'] == 900

    def test_module_constants_match_a_fresh_load(self, monkeypatch):
        monkeypatch.delenv('SETHLANS_UNREAL_ENABLED', raising=False)
        assert config_unreal.ENABLED is False
        assert config_unreal.SHARE == ''

    def test_no_drain_settings(self):
        assert not [name for name in dir(config_unreal) if name.startswith('DRAIN')]


class TestShareIsNotReadWhileDisabled:

    def test_disabled_never_asks_for_share(self):
        get_value = getter({'enabled': 'false', 'share': r'\\nas\sethlans'})
        values = config_unreal.load(get_value)
        assert values['SHARE'] == ''
        assert 'share' not in get_value.asked

    def test_enabled_reads_and_strips_share(self):
        get_value = getter({'enabled': 'true', 'share': '  \\\\nas\\sethlans  '})
        assert config_unreal.load(get_value)['SHARE'] == r'\\nas\sethlans'
        assert 'share' in get_value.asked


class TestMalformedValuesFallBack:

    @pytest.mark.parametrize('raw', ['abc', '', '1.5', True, None, [1]])
    def test_non_integer(self, raw, caplog):
        values = config_unreal.load(getter({'vram_sample_seconds': raw}))
        assert values['VRAM_SAMPLE_SECONDS'] == 30

    @pytest.mark.parametrize('key,raw,expected', [
        ('vram_sample_seconds', '4', 30), ('vram_sample_seconds', '601', 30),
        ('vram_sample_seconds', '5', 5), ('vram_sample_seconds', 600, 600),
        ('share_recheck_seconds', '9', 60), ('share_recheck_seconds', '3600', 3600),
        ('share_recheck_max_seconds', '59', 900), ('share_recheck_max_seconds', '86401', 900),
        ('share_recheck_max_seconds', '86400', 86400),
        ('vram_mb', '-1', 0), ('vram_mb', '1048577', 0), ('vram_mb', '16384', 16384),
    ])
    def test_bounds(self, key, raw, expected):
        assert config_unreal.load(getter({key: raw}))[key.upper()] == expected

    def test_max_below_base_is_raised_to_base(self, caplog):
        values = config_unreal.load(getter({'share_recheck_seconds': '600',
                                            'share_recheck_max_seconds': '120'}))
        assert values['SHARE_RECHECK_MAX_SECONDS'] == 600
        assert 'share_recheck_max_seconds' in caplog.text

    def test_hang_must_exceed_warmup_hold(self):
        values = config_unreal.load(getter({'warmup_hold_seconds': '1000', 'hang_seconds': '900'}))
        assert values['HANG_SECONDS'] == 1061

    @pytest.mark.parametrize('raw,expected', [
        ('true', True), ('1', True), ('YES', True), ('false', False), ('0', False),
        (True, True), ('maybe', False),
    ])
    def test_enabled_parsing(self, raw, expected):
        assert config_unreal.load(getter({'enabled': raw}))['ENABLED'] is expected

    def test_bad_workcopy_mode(self):
        assert config_unreal.load(getter({'workcopy_mode': 'symlink'}))['WORKCOPY_MODE'] == 'hardlink'
        assert config_unreal.load(getter({'workcopy_mode': 'COPY'}))['WORKCOPY_MODE'] == 'copy'

    def test_getter_raising_never_escapes(self):
        def broken(section, key, default):
            raise ValueError('boom')

        values = config_unreal.load(broken)
        assert values['ENABLED'] is False and values['CACHE_DIR']

    def test_real_env_override(self, monkeypatch):
        monkeypatch.setenv('SETHLANS_UNREAL_VRAM_SAMPLE_SECONDS', 'nope')
        monkeypatch.setenv('SETHLANS_UNREAL_RESCAN_SECONDS', '120')
        values = config_unreal.load()
        assert values['VRAM_SAMPLE_SECONDS'] == 30
        assert values['RESCAN_SECONDS'] == 120


class TestForceGpuIndex:

    @pytest.mark.parametrize('value,expected', [(None, False), ('', False), ('  ', False),
                                                ('0', True), ('1', True), ('x', True)])
    def test_force_gpu_index_set(self, monkeypatch, value, expected):
        monkeypatch.setattr(config_unreal.config, 'FORCE_GPU_INDEX', value)
        assert config_unreal.force_gpu_index_set() is expected
