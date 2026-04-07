# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker agent config module.

Tests the configuration loading hierarchy: env vars > config.ini > defaults,
plus validation helpers and the FR-26 GPU_MODE parsing rules.
"""
import configparser
import importlib
import sys

import pytest

from sethlans_worker_agent.config import (
    get_config_value,
    _validate_int,
    _validate_non_empty_string,
)


def _reload_config():
    """Reload the worker config module so env vars re-parse at module load."""
    import sethlans_worker_agent.config as cfg
    return importlib.reload(cfg)


def _clear_gpu_mode_env(monkeypatch):
    """Ensure a clean baseline for every GPU_MODE parsing test."""
    monkeypatch.delenv('SETHLANS_GPU_MODE', raising=False)
    monkeypatch.delenv('SETHLANS_GPU_SPLIT_MODE', raising=False)
    monkeypatch.delenv('SETHLANS_FORCE_GPU_INDEX', raising=False)
    monkeypatch.delenv('SETHLANS_FORCE_CPU_ONLY', raising=False)
    monkeypatch.delenv('SETHLANS_FORCE_GPU_ONLY', raising=False)


# --- get_config_value ---

class TestGetConfigValue:

    def test_returns_default_when_no_env_or_ini(self, mocker):
        mocker.patch('os.getenv', return_value=None)
        mocker.patch.object(
            configparser.ConfigParser, 'has_option', return_value=False
        )
        assert get_config_value('test', 'key', 'fallback') == 'fallback'

    def test_returns_ini_value_when_no_env(self, mocker):
        mocker.patch('os.getenv', return_value=None)
        mocker.patch.object(
            configparser.ConfigParser, 'has_option', return_value=True
        )
        mocker.patch.object(
            configparser.ConfigParser, 'get', return_value='from_ini'
        )
        assert get_config_value('section', 'key', 'default') == 'from_ini'

    def test_env_var_overrides_ini(self, mocker):
        mocker.patch('os.getenv', return_value='from_env')
        mocker.patch.object(
            configparser.ConfigParser, 'has_option', return_value=True
        )
        mocker.patch.object(
            configparser.ConfigParser, 'get', return_value='from_ini'
        )
        assert get_config_value('s', 'k', 'default') == 'from_env'

    def test_env_var_name_is_uppercased(self, mocker):
        spy = mocker.patch('os.getenv', return_value=None)
        mocker.patch.object(
            configparser.ConfigParser, 'has_option', return_value=False
        )
        get_config_value('manager', 'port', '7075')
        spy.assert_called_once_with('SETHLANS_MANAGER_PORT')

    def test_is_int_converts_env_value(self, mocker):
        mocker.patch('os.getenv', return_value='9090')
        result = get_config_value('m', 'p', 7075, is_int=True)
        assert result == 9090
        assert isinstance(result, int)

    def test_is_int_converts_ini_value(self, mocker):
        mocker.patch('os.getenv', return_value=None)
        mocker.patch.object(
            configparser.ConfigParser, 'has_option', return_value=True
        )
        mocker.patch.object(
            configparser.ConfigParser, 'getint', return_value=8080
        )
        result = get_config_value('s', 'k', 7075, is_int=True)
        assert result == 8080

    def test_is_int_converts_default(self, mocker):
        mocker.patch('os.getenv', return_value=None)
        mocker.patch.object(
            configparser.ConfigParser, 'has_option', return_value=False
        )
        result = get_config_value('s', 'k', '7075', is_int=True)
        assert result == 7075
        assert isinstance(result, int)


# --- _validate_int ---

class TestValidateInt:

    def test_valid_value_within_bounds(self):
        assert _validate_int('x', 50, 10, min_val=1, max_val=100) == 50

    def test_value_below_minimum_returns_default(self):
        assert _validate_int('x', 0, 10, min_val=1) == 10

    def test_value_above_maximum_returns_default(self):
        assert _validate_int('x', 200, 10, max_val=100) == 10

    def test_non_integer_returns_default(self):
        assert _validate_int('x', 'abc', 10) == 10

    def test_no_bounds_passes_through(self):
        assert _validate_int('x', 999, 10) == 999

    def test_exact_minimum_is_valid(self):
        assert _validate_int('x', 1, 10, min_val=1) == 1

    def test_exact_maximum_is_valid(self):
        assert _validate_int('x', 100, 10, max_val=100) == 100


# --- _validate_non_empty_string ---

class TestValidateNonEmptyString:

    def test_valid_string(self):
        assert _validate_non_empty_string('h', '192.168.1.1', '127.0.0.1') == '192.168.1.1'

    def test_empty_string_returns_default(self):
        assert _validate_non_empty_string('h', '', '127.0.0.1') == '127.0.0.1'

    def test_whitespace_only_returns_default(self):
        assert _validate_non_empty_string('h', '   ', '127.0.0.1') == '127.0.0.1'

    def test_non_string_returns_default(self):
        assert _validate_non_empty_string('h', 12345, '127.0.0.1') == '127.0.0.1'

    def test_none_returns_default(self):
        assert _validate_non_empty_string('h', None, '127.0.0.1') == '127.0.0.1'


# --- FR-26: GPU_MODE parsing ---

class TestGpuModeParsing:
    """Validate the top-of-module env parsing by reloading the config
    module after manipulating SETHLANS_GPU_MODE and related env vars.
    """

    def test_default_gpu_mode_is_split(self, monkeypatch):
        _clear_gpu_mode_env(monkeypatch)
        cfg = _reload_config()
        assert cfg.GPU_MODE == 'split'

    def test_gpu_mode_split_is_parsed(self, monkeypatch):
        _clear_gpu_mode_env(monkeypatch)
        monkeypatch.setenv('SETHLANS_GPU_MODE', 'split')
        cfg = _reload_config()
        assert cfg.GPU_MODE == 'split'

    def test_gpu_mode_combined_is_parsed(self, monkeypatch):
        _clear_gpu_mode_env(monkeypatch)
        monkeypatch.setenv('SETHLANS_GPU_MODE', 'combined')
        cfg = _reload_config()
        assert cfg.GPU_MODE == 'combined'

    def test_gpu_mode_is_case_insensitive(self, monkeypatch):
        _clear_gpu_mode_env(monkeypatch)
        monkeypatch.setenv('SETHLANS_GPU_MODE', 'SPLIT')
        cfg = _reload_config()
        assert cfg.GPU_MODE == 'split'

        _clear_gpu_mode_env(monkeypatch)
        monkeypatch.setenv('SETHLANS_GPU_MODE', 'Combined')
        cfg = _reload_config()
        assert cfg.GPU_MODE == 'combined'

    def test_invalid_gpu_mode_exits_non_zero(self, monkeypatch):
        _clear_gpu_mode_env(monkeypatch)
        monkeypatch.setenv('SETHLANS_GPU_MODE', 'invalid')
        with pytest.raises(SystemExit) as excinfo:
            _reload_config()
        assert excinfo.value.code != 0

    def test_force_gpu_index_with_combined_mode_exits_non_zero(
        self, monkeypatch
    ):
        _clear_gpu_mode_env(monkeypatch)
        monkeypatch.setenv('SETHLANS_GPU_MODE', 'combined')
        monkeypatch.setenv('SETHLANS_FORCE_GPU_INDEX', '0')
        with pytest.raises(SystemExit) as excinfo:
            _reload_config()
        assert excinfo.value.code != 0

    def test_legacy_split_mode_env_var_logs_warning(self, monkeypatch, caplog):
        _clear_gpu_mode_env(monkeypatch)
        monkeypatch.setenv('SETHLANS_GPU_SPLIT_MODE', 'true')
        caplog.set_level('WARNING', logger='sethlans_worker_agent.config')
        cfg = _reload_config()
        # Startup continues on the default.
        assert cfg.GPU_MODE == 'split'
        warning_msgs = [
            r.message for r in caplog.records if r.levelname == 'WARNING'
        ]
        assert any(
            'SETHLANS_GPU_SPLIT_MODE' in m and 'no longer recognized' in m
            for m in warning_msgs
        )


@pytest.fixture(autouse=True, scope='module')
def _restore_config_module_at_end():
    """Ensure the config module is restored to its original state after
    this file's reload-based tests run, so other tests still see a
    canonical config module."""
    yield
    if 'sethlans_worker_agent.config' in sys.modules:
        try:
            importlib.reload(sys.modules['sethlans_worker_agent.config'])
        except SystemExit:
            pass
