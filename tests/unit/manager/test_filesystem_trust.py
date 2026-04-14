# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/workers/services/filesystem_trust.py``.

Covers get_worker_config_path(), write_worker_config(), URL parsing,
merge with existing config, parent directory creation, durable write.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from workers.services.filesystem_trust import (
    get_worker_config_path,
    write_worker_config,
)


# ---- Fixtures -------------------------------------------------------------


@pytest.fixture
def config_dir(tmp_path):
    """Return a fresh directory to use as SETHLANS_WORKER_DATA_DIR."""
    d = tmp_path / "worker_data"
    d.mkdir()
    return d


@pytest.fixture
def config_path(tmp_path):
    """Return a config.json path inside tmp_path (file does not exist)."""
    return tmp_path / "worker" / "config.json"


SAMPLE_TOKEN = "abc123token"
SAMPLE_FINGERPRINT = "aa" * 32
SAMPLE_URL = "https://127.0.0.1:8080"
SAMPLE_MANAGER_ID = "mgr-uuid-001"


# ---- get_worker_config_path() ---------------------------------------------


class TestGetWorkerConfigPath:

    def test_returns_path_ending_in_config_json(self, config_dir):
        with patch.dict(
            os.environ,
            {"SETHLANS_WORKER_DATA_DIR": str(config_dir)},
        ):
            result = get_worker_config_path()
        assert result.name == "config.json"
        assert isinstance(result, Path)

    def test_env_override_respected(self, config_dir):
        with patch.dict(
            os.environ,
            {"SETHLANS_WORKER_DATA_DIR": str(config_dir)},
        ):
            result = get_worker_config_path()
        assert result == config_dir / "config.json"

    def test_creates_directory_if_missing(self, tmp_path):
        target = tmp_path / "does_not_exist"
        assert not target.exists()
        with patch.dict(
            os.environ,
            {"SETHLANS_WORKER_DATA_DIR": str(target)},
        ):
            result = get_worker_config_path()
        assert target.exists()
        assert result.parent == target

    def test_relative_path_raises_value_error(self):
        with patch.dict(
            os.environ,
            {"SETHLANS_WORKER_DATA_DIR": "relative/path"},
        ):
            with pytest.raises(ValueError, match="absolute"):
                get_worker_config_path()


# ---- write_worker_config() — JSON structure --------------------------------


class TestWriteWorkerConfigStructure:

    def test_writes_valid_json(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_manager_section_keys(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        mgr = data["manager"]
        assert mgr["api_token"] == SAMPLE_TOKEN
        assert mgr["cert_fingerprint"] == SAMPLE_FINGERPRINT
        assert mgr["manager_id"] == SAMPLE_MANAGER_ID
        assert mgr["host"] == "127.0.0.1"
        assert mgr["port"] == 8080

    def test_enrollment_section(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["enrollment"]["wizard_complete"] is True

    def test_json_indent_and_sort_keys(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        text = config_path.read_text(encoding="utf-8")
        # Re-serialize with known format and compare
        data = json.loads(text)
        expected = json.dumps(data, indent=2, sort_keys=True)
        assert text.strip() == expected.strip()


# ---- write_worker_config() — URL parsing -----------------------------------


class TestWriteWorkerConfigUrlParsing:

    def test_extracts_host_and_port(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            "https://192.168.1.50:9090", SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["manager"]["host"] == "192.168.1.50"
        assert data["manager"]["port"] == 9090

    def test_missing_port_defaults_to_8080(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            "https://10.0.0.1", SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["manager"]["port"] == 8080

    def test_hostname_url(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            "https://my-manager.local:8443", SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["manager"]["host"] == "my-manager.local"
        assert data["manager"]["port"] == 8443

    def test_missing_host_defaults_to_loopback(self, config_path):
        # urlparse("https://") yields hostname=None
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            "https://", SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["manager"]["host"] == "127.0.0.1"


# ---- write_worker_config() — merge with existing config --------------------


class TestWriteWorkerConfigMerge:

    def test_preserves_existing_keys_outside_manager_enrollment(
        self, config_path,
    ):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "blender": {"version": "4.2.19"},
            "worker": {"gpu": "RTX 4090"},
        }
        config_path.write_text(
            json.dumps(existing), encoding="utf-8",
        )

        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["blender"]["version"] == "4.2.19"
        assert data["worker"]["gpu"] == "RTX 4090"
        assert "manager" in data
        assert "enrollment" in data

    def test_overwrites_existing_manager_section(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        old = {
            "manager": {
                "api_token": "old_token",
                "host": "old.host",
                "port": 1234,
            },
        }
        config_path.write_text(
            json.dumps(old), encoding="utf-8",
        )

        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["manager"]["api_token"] == SAMPLE_TOKEN
        assert data["manager"]["host"] == "127.0.0.1"

    def test_handles_corrupt_existing_json(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("NOT VALID JSON", encoding="utf-8")

        # Should not raise — the corrupt file is logged and ignored
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["manager"]["api_token"] == SAMPLE_TOKEN

    def test_handles_existing_non_dict_json(self, config_path):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("[1, 2, 3]", encoding="utf-8")

        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["manager"]["api_token"] == SAMPLE_TOKEN


# ---- write_worker_config() — directory creation ----------------------------


class TestWriteWorkerConfigDirectoryCreation:

    def test_creates_parent_directories(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "config.json"
        assert not deep_path.parent.exists()

        write_worker_config(
            deep_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        assert deep_path.exists()


# ---- Durable write (tested indirectly) ------------------------------------


class TestDurableWrite:

    def test_file_is_readable_after_write(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["manager"]["api_token"] == SAMPLE_TOKEN

    def test_content_roundtrips_through_json(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        text = config_path.read_text(encoding="utf-8")
        # Must be valid JSON that roundtrips cleanly
        data = json.loads(text)
        re_serialized = json.dumps(data, indent=2, sort_keys=True)
        assert text.strip() == re_serialized.strip()

    def test_no_temp_files_left_behind(self, config_path):
        write_worker_config(
            config_path, SAMPLE_TOKEN, SAMPLE_FINGERPRINT,
            SAMPLE_URL, SAMPLE_MANAGER_ID,
        )
        parent_files = list(config_path.parent.iterdir())
        assert parent_files == [config_path]
