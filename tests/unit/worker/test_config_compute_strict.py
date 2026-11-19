# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the strict read of the worker config (C-20).

``config_store.load_strict`` on real files in ``tmp_path``: a missing file is
``{}``, an unreadable or unparsable one raises ``ConfigReadError`` (an
``OSError``) where ``load`` returns ``{}``. ``config_compute.load`` turns that
error into defaults marked ``read_failed``; ``load(strict=True)`` raises it.
"""
import pytest

from sethlans_worker_agent import config_compute, config_store


@pytest.fixture
def user_file(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    monkeypatch.setattr(config_store, 'user_config_path', lambda: path)
    monkeypatch.setattr(config_store, 'load_system_config', lambda: {})
    return path


class TestLoadStrict:

    def test_missing_file_is_empty(self, user_file):
        assert config_store.load_strict() == {}

    def test_valid_file(self, user_file):
        user_file.write_text('{"compute": {"method": "gpu"}}', encoding='utf-8')
        assert config_store.load_strict() == {'compute': {'method': 'gpu'}}

    @pytest.mark.parametrize('content', ['{"compute": ', '[1, 2]', '\xff\xfe'], ids=['truncated', 'not-object', 'bytes'])
    def test_unparsable_file_raises(self, user_file, content):
        user_file.write_bytes(content.encode('latin-1'))
        with pytest.raises(config_store.ConfigReadError):
            config_store.load_strict()

    def test_unreadable_path_raises(self, user_file):
        user_file.mkdir()  # a directory where the file should be: open() fails with an OSError
        with pytest.raises(config_store.ConfigReadError):
            config_store.load_strict()

    def test_error_is_an_oserror(self):
        assert issubclass(config_store.ConfigReadError, OSError)


class TestComputeLoad:

    @pytest.fixture
    def unreadable(self, monkeypatch):
        def read_raw():
            raise config_store.ConfigReadError('could not read config.json: busy')
        monkeypatch.setattr(config_compute, '_read_raw', read_raw)

    def test_failed_read_gives_defaults_marked_read_failed(self, unreadable, caplog):
        section = config_compute.load()
        assert section.read_failed is True
        assert (section.method, section.revision, section.cards, section.alerts) == ('cpu', 0, (), ())
        assert any('could not be read' in record.getMessage() for record in caplog.records)

    def test_failed_read_keeps_the_environment(self, unreadable, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_METHOD, 'gpu')
        section = config_compute.load()
        assert section.read_failed is True and section.env_override is True and section.method == 'gpu'

    def test_strict_load_raises(self, unreadable):
        with pytest.raises(config_store.ConfigReadError):
            config_compute.load(strict=True)

    def test_good_read_is_not_marked(self, monkeypatch):
        monkeypatch.setattr(config_compute, '_read_raw', lambda: {'method': 'gpu', 'revision': 3})
        section = config_compute.load(strict=True)
        assert section.read_failed is False and (section.method, section.revision) == ('gpu', 3)
