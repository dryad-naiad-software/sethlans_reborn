# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``[gpu_memory] source`` (GPU memory reader FR-GS-1)."""
import importlib
import logging

import pytest

from sethlans_worker_agent import config_gpu_memory
from sethlans_worker_agent.gpu_memory import reader as reader_module

ENV = 'SETHLANS_GPU_MEMORY_SOURCE'


def getter(value):
    calls = []

    def get(section, key, default):
        calls.append((section, key, default))
        return value

    get.calls = calls
    return get


class TestLoadSource:

    @pytest.mark.parametrize('value', ['auto', 'pdh', 'nvidia-smi', 'amdgpu-sysfs', 'none'])
    def test_every_allowed_value(self, value, caplog):
        assert config_gpu_memory.load_source(getter(value)) == value
        assert not [record for record in caplog.records if record.levelno >= logging.WARNING]

    def test_reads_the_gpu_memory_section_with_auto_as_default(self):
        get = getter('auto')
        config_gpu_memory.load_source(get)
        assert get.calls == [('gpu_memory', 'source', 'auto')]

    @pytest.mark.parametrize('raw,expected', [(' NVIDIA-SMI ', 'nvidia-smi'), ('None', 'none'), ('AUTO\n', 'auto')])
    def test_case_and_spaces_are_ignored(self, raw, expected):
        assert config_gpu_memory.load_source(getter(raw)) == expected

    @pytest.mark.parametrize('raw', ['cuda', '', 'nvidia smi', 'pdh,nvidia-smi', 5, None, ['pdh']])
    def test_an_invalid_value_warns_and_counts_as_auto(self, raw, caplog):
        assert config_gpu_memory.load_source(getter(raw)) == 'auto'
        [warning] = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert warning.getMessage().startswith(f"Config 'gpu_memory.source' has invalid value {raw!r}")

    def test_a_getter_that_raises_warns_and_counts_as_auto(self, caplog):
        def broken(section, key, default):
            raise ValueError('unreadable store')

        assert config_gpu_memory.load_source(broken) == 'auto'
        assert any('could not be read' in message for message in caplog.messages)

    def test_the_env_var_is_read_through_the_worker_config(self, monkeypatch):
        monkeypatch.setenv(ENV, 'none')
        assert config_gpu_memory.load_source() == 'none'

    def test_an_invalid_env_var_counts_as_auto(self, monkeypatch):
        monkeypatch.setenv(ENV, 'bogus')
        assert config_gpu_memory.load_source() == 'auto'


class TestReadOnce:

    def test_source_is_fixed_at_import(self, monkeypatch):
        try:
            monkeypatch.setenv(ENV, 'nvidia-smi')
            importlib.reload(config_gpu_memory)
            assert config_gpu_memory.SOURCE == 'nvidia-smi'
            monkeypatch.setenv(ENV, 'none')
            assert config_gpu_memory.SOURCE == 'nvidia-smi'  # not read again
            assert reader_module._configured_source() == 'nvidia-smi'
        finally:
            monkeypatch.delenv(ENV, raising=False)
            importlib.reload(config_gpu_memory)

    def test_the_value_is_always_allowed(self):
        assert config_gpu_memory.SOURCE in config_gpu_memory.SOURCES
