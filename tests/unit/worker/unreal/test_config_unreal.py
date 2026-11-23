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


class TestRamSettings:
    """Render per card FR-PC-RAM-7: the RAM gate's reserves and defaults (MB), measured with two 20 GB AMD cards."""

    NAMES = ('ram_reserve_mb', 'commit_reserve_mb', 'ram_default_ws_mb', 'ram_default_commit_mb', 'ram_blender_mb')

    def test_defaults(self):
        values = config_unreal.load(getter({}))
        assert [values[name.upper()] for name in self.NAMES] == [4096, 8192, 16384, 32768, 4096]
        assert config_unreal.RAM_DEFAULT_COMMIT_MB == values['RAM_DEFAULT_COMMIT_MB']

    @pytest.mark.parametrize('name', NAMES)
    def test_bounds_and_bad_values_fall_back_with_a_warning(self, name, caplog):
        default = config_unreal.load(getter({}))[name.upper()]
        assert config_unreal.load(getter({name: '0'}))[name.upper()] == 0
        assert config_unreal.load(getter({name: '1048576'}))[name.upper()] == 1048576
        for raw in ('-1', '1048577', 'lots'):
            caplog.clear()
            assert config_unreal.load(getter({name: raw}))[name.upper()] == default
            assert f'unreal.{name}' in caplog.text

    @pytest.mark.parametrize('name', NAMES)
    def test_the_env_var_names(self, name, monkeypatch):
        monkeypatch.setenv(f'SETHLANS_UNREAL_{name.upper()}', '12000')
        assert config_unreal.load()[name.upper()] == 12000


class TestSpillMargin:
    """Per-machine memory test FR-MT-S-2 (D9): ``unreal.spill_margin_mb``, 0-65536, default 256."""

    def test_the_default(self):
        assert config_unreal.load(getter({}))['SPILL_MARGIN_MB'] == 256
        assert isinstance(config_unreal.SPILL_MARGIN_MB, int)

    @pytest.mark.parametrize('raw,expected', [('0', 0), ('65536', 65536), (512, 512), (' 300 ', 300)])
    def test_values_in_range(self, raw, expected):
        assert config_unreal.load(getter({'spill_margin_mb': raw}))['SPILL_MARGIN_MB'] == expected

    @pytest.mark.parametrize('raw', ['-1', '65537', 'lots', '1.5', True])
    def test_out_of_range_or_non_integer_warns_and_uses_256(self, raw, caplog):
        assert config_unreal.load(getter({'spill_margin_mb': raw}))['SPILL_MARGIN_MB'] == 256
        assert [r for r in caplog.records if r.levelname == 'WARNING' and 'unreal.spill_margin_mb' in r.getMessage()]

    def test_the_env_var(self, monkeypatch):
        monkeypatch.setenv('SETHLANS_UNREAL_SPILL_MARGIN_MB', '1024')
        assert config_unreal.load()['SPILL_MARGIN_MB'] == 1024

    def test_a_bad_env_var_warns_and_uses_256(self, monkeypatch, caplog):
        monkeypatch.setenv('SETHLANS_UNREAL_SPILL_MARGIN_MB', '70000')
        assert config_unreal.load()['SPILL_MARGIN_MB'] == 256 and 'unreal.spill_margin_mb' in caplog.text

    def test_the_config_ini_value(self, monkeypatch):
        import configparser

        from sethlans_worker_agent import config
        parser = configparser.ConfigParser()
        parser.read_string('[unreal]\nspill_margin_mb = 640\n')
        monkeypatch.delenv('SETHLANS_UNREAL_SPILL_MARGIN_MB', raising=False)
        monkeypatch.setattr(config, '_json_config', {})
        monkeypatch.setattr(config, 'config_parser', parser)
        assert config_unreal.load()['SPILL_MARGIN_MB'] == 640

    def test_the_example_ini_documents_it(self):
        from pathlib import Path
        example = Path(config_unreal.__file__).resolve().parents[1] / 'config.ini.example'
        assert '# spill_margin_mb = 256' in example.read_text(encoding='utf-8')


class TestPilotOthersMargin:
    """Owner decision Q10: ``unreal.pilot_others_margin_mb``, 0-65536, default 512; the spill margin stays 256."""

    def test_the_default_and_the_spill_margin_untouched(self):
        values = config_unreal.load(getter({}))
        assert (values['PILOT_OTHERS_MARGIN_MB'], values['SPILL_MARGIN_MB']) == (512, 256)
        assert isinstance(config_unreal.PILOT_OTHERS_MARGIN_MB, int)

    def test_each_key_is_read_on_its_own(self):
        values = config_unreal.load(getter({'pilot_others_margin_mb': 2048, 'spill_margin_mb': 128}))
        assert (values['PILOT_OTHERS_MARGIN_MB'], values['SPILL_MARGIN_MB']) == (2048, 128)

    @pytest.mark.parametrize('raw', ['-1', '65537', 'lots', True])
    def test_out_of_range_or_non_integer_warns_and_uses_512(self, raw, caplog):
        assert config_unreal.load(getter({'pilot_others_margin_mb': raw}))['PILOT_OTHERS_MARGIN_MB'] == 512
        assert 'unreal.pilot_others_margin_mb' in caplog.text

    def test_the_env_var_sets_only_this_margin(self, monkeypatch):
        import configparser

        from sethlans_worker_agent import config
        monkeypatch.setenv('SETHLANS_UNREAL_PILOT_OTHERS_MARGIN_MB', '1024')
        monkeypatch.delenv('SETHLANS_UNREAL_SPILL_MARGIN_MB', raising=False)
        monkeypatch.setattr(config, '_json_config', {})
        monkeypatch.setattr(config, 'config_parser', configparser.ConfigParser())
        values = config_unreal.load()
        assert (values['PILOT_OTHERS_MARGIN_MB'], values['SPILL_MARGIN_MB']) == (1024, 256)

    def test_a_bad_env_var_warns_and_uses_512(self, monkeypatch, caplog):
        monkeypatch.setenv('SETHLANS_UNREAL_PILOT_OTHERS_MARGIN_MB', '70000')
        assert config_unreal.load()['PILOT_OTHERS_MARGIN_MB'] == 512 and 'unreal.pilot_others_margin_mb' in caplog.text

    def test_the_config_ini_value(self, monkeypatch):
        import configparser

        from sethlans_worker_agent import config
        parser = configparser.ConfigParser()
        parser.read_string('[unreal]\npilot_others_margin_mb = 768\n')
        monkeypatch.delenv('SETHLANS_UNREAL_PILOT_OTHERS_MARGIN_MB', raising=False)
        monkeypatch.delenv('SETHLANS_UNREAL_SPILL_MARGIN_MB', raising=False)
        monkeypatch.setattr(config, '_json_config', {})
        monkeypatch.setattr(config, 'config_parser', parser)
        values = config_unreal.load()
        assert (values['PILOT_OTHERS_MARGIN_MB'], values['SPILL_MARGIN_MB']) == (768, 256)

    def test_the_example_ini_documents_it_next_to_the_spill_margin(self):
        from pathlib import Path
        example = Path(config_unreal.__file__).resolve().parents[1] / 'config.ini.example'
        text = example.read_text(encoding='utf-8')
        assert '# pilot_others_margin_mb = 512' in text and 'SETHLANS_UNREAL_PILOT_OTHERS_MARGIN_MB' in text
        assert 0 < text.index('# pilot_others_margin_mb = 512') - text.index('# spill_margin_mb = 256') < 800
