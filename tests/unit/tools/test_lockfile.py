# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``tools/caddy_fetch/lockfile.py``.

Covers lockfile loading & validation, host-platform detection, and URL
construction.  No network / disk IO beyond tmp_path and the committed
``tools/caddy.lock``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from caddy_fetch import lockfile as lock_mod


VALID_LOCK = {
    "version": "2.8.4",
    "url_template": (
        "https://example.test/download/v{version}/"
        "caddy_{version}_{release_platform}{ext}"
    ),
    "platforms": {
        "linux-amd64": {
            "release_platform": "linux_amd64",
            "ext": ".tar.gz",
            "sha256": "a" * 64,
        },
        "windows-amd64": {
            "release_platform": "windows_amd64",
            "ext": ".zip",
            "sha256": "b" * 64,
        },
    },
}


def _write_lock(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "caddy.lock"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---- load_lockfile --------------------------------------------------------


class TestLoadLockfile:

    def test_returns_parsed_data_for_valid_lockfile(self, tmp_path):
        path = _write_lock(tmp_path, VALID_LOCK)
        data = lock_mod.load_lockfile(path)
        assert data["version"] == "2.8.4"
        assert "linux-amd64" in data["platforms"]
        assert data["platforms"]["linux-amd64"]["sha256"] == "a" * 64

    def test_defaults_to_repo_lockfile_when_path_none(self):
        # Smoke test: the committed tools/caddy.lock is readable and valid.
        data = lock_mod.load_lockfile(None)
        assert "version" in data
        assert "platforms" in data
        assert "linux-amd64" in data["platforms"]

    def test_missing_file_raises_file_not_found(self, tmp_path):
        missing = tmp_path / "does-not-exist.lock"
        with pytest.raises(FileNotFoundError, match="caddy.lock"):
            lock_mod.load_lockfile(missing)

    def test_malformed_json_raises_json_decode_error(self, tmp_path):
        path = tmp_path / "caddy.lock"
        path.write_text("{ this is not json ", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            lock_mod.load_lockfile(path)

    @pytest.mark.parametrize(
        "missing_key", ["version", "url_template", "platforms"],
    )
    def test_missing_top_level_keys_raise_value_error(
        self, tmp_path, missing_key,
    ):
        data = {k: v for k, v in VALID_LOCK.items() if k != missing_key}
        path = _write_lock(tmp_path, data)
        with pytest.raises(ValueError, match="missing keys"):
            lock_mod.load_lockfile(path)

    def test_non_https_url_template_raises(self, tmp_path):
        bad = dict(VALID_LOCK)
        bad["url_template"] = "http://example.test/{version}/{platform}{ext}"
        path = _write_lock(tmp_path, bad)
        with pytest.raises(ValueError, match="HTTPS-only"):
            lock_mod.load_lockfile(path)

    def test_empty_platforms_raises(self, tmp_path):
        bad = dict(VALID_LOCK)
        bad["platforms"] = {}
        path = _write_lock(tmp_path, bad)
        with pytest.raises(ValueError, match="non-empty"):
            lock_mod.load_lockfile(path)

    def test_platforms_not_a_dict_raises(self, tmp_path):
        bad = dict(VALID_LOCK)
        bad["platforms"] = ["linux-amd64"]
        path = _write_lock(tmp_path, bad)
        with pytest.raises(ValueError, match="non-empty object"):
            lock_mod.load_lockfile(path)

    def test_platform_entry_missing_ext_raises(self, tmp_path):
        bad = json.loads(json.dumps(VALID_LOCK))  # deep copy
        del bad["platforms"]["linux-amd64"]["ext"]
        path = _write_lock(tmp_path, bad)
        with pytest.raises(ValueError, match="'ext'"):
            lock_mod.load_lockfile(path)

    def test_platform_entry_missing_sha256_raises(self, tmp_path):
        bad = json.loads(json.dumps(VALID_LOCK))
        del bad["platforms"]["linux-amd64"]["sha256"]
        path = _write_lock(tmp_path, bad)
        with pytest.raises(ValueError, match="'sha256'"):
            lock_mod.load_lockfile(path)


# ---- detect_host_platform -------------------------------------------------


class TestDetectHostPlatform:

    @pytest.mark.parametrize(
        ("system", "machine", "expected"),
        [
            ("Linux", "x86_64", "linux-amd64"),
            ("Linux", "amd64", "linux-amd64"),
            ("Linux", "aarch64", "linux-arm64"),
            ("Linux", "arm64", "linux-arm64"),
            ("Darwin", "x86_64", "darwin-amd64"),
            ("Darwin", "arm64", "darwin-arm64"),
            ("Windows", "AMD64", "windows-amd64"),
        ],
    )
    def test_known_host_combos(self, mocker, system, machine, expected):
        mocker.patch(
            "caddy_fetch.lockfile.host_platform.system", return_value=system,
        )
        mocker.patch(
            "caddy_fetch.lockfile.host_platform.machine", return_value=machine,
        )
        assert lock_mod.detect_host_platform() == expected

    def test_unknown_arch_raises(self, mocker):
        mocker.patch(
            "caddy_fetch.lockfile.host_platform.system", return_value="Linux",
        )
        mocker.patch(
            "caddy_fetch.lockfile.host_platform.machine",
            return_value="sparc64",
        )
        with pytest.raises(ValueError, match="architecture"):
            lock_mod.detect_host_platform()

    def test_unknown_os_raises(self, mocker):
        mocker.patch(
            "caddy_fetch.lockfile.host_platform.system", return_value="FreeBSD",
        )
        mocker.patch(
            "caddy_fetch.lockfile.host_platform.machine", return_value="x86_64",
        )
        with pytest.raises(ValueError, match="OS"):
            lock_mod.detect_host_platform()


# ---- build_url ------------------------------------------------------------


class TestBuildUrl:

    def test_interpolates_version_and_release_platform(self):
        url = lock_mod.build_url(VALID_LOCK, "linux-amd64")
        assert url == (
            "https://example.test/download/v2.8.4/"
            "caddy_2.8.4_linux_amd64.tar.gz"
        )

    def test_uses_zip_ext_for_windows(self):
        url = lock_mod.build_url(VALID_LOCK, "windows-amd64")
        assert url.endswith("_windows_amd64.zip")

    def test_falls_back_to_platform_key_when_no_release_platform(self):
        lock = json.loads(json.dumps(VALID_LOCK))
        del lock["platforms"]["linux-amd64"]["release_platform"]
        # Substitute {release_platform} with {platform} behavior: the
        # template still contains {release_platform}, but build_url
        # defaults release_platform -> platform_key when the entry
        # omits it.
        url = lock_mod.build_url(lock, "linux-amd64")
        assert "linux-amd64" in url

    def test_unknown_platform_raises(self):
        with pytest.raises(ValueError, match="not in caddy.lock"):
            lock_mod.build_url(VALID_LOCK, "haiku-ppc")

    def test_non_https_constructed_url_raises(self):
        bad = json.loads(json.dumps(VALID_LOCK))
        # Bypass load_lockfile's up-front HTTPS check by mutating a
        # pre-loaded dict.
        bad["url_template"] = "http://evil.test/{version}/{ext}"
        with pytest.raises(ValueError, match="not HTTPS"):
            lock_mod.build_url(bad, "linux-amd64")
