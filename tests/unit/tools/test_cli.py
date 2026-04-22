# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``tools/caddy_fetch/cli.py``.

Covers argparse wiring + exit-code mapping (0 / 1 / 2 / 3).  All heavy
lifting (``fetch_and_install``, ``verify_gpg``, ``load_lockfile``) is
mocked at the module boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import urllib.error

from caddy_fetch import cli as cli_mod
from caddy_fetch.exceptions import GpgVerificationError, IntegrityError


VALID_LOCK = {
    "version": "2.8.4",
    "url_template": (
        "https://example.test/v{version}/caddy_{release_platform}{ext}"
    ),
    "platforms": {
        "linux-amd64": {
            "release_platform": "linux_amd64",
            "ext": ".tar.gz",
            "sha256": "a" * 64,
        },
        "linux-arm64": {
            "release_platform": "linux_arm64",
            "ext": ".tar.gz",
            "sha256": "b" * 64,
        },
        "windows-amd64": {
            "release_platform": "windows_amd64",
            "ext": ".zip",
            "sha256": "c" * 64,
        },
    },
}


@pytest.fixture
def lockfile_path(tmp_path) -> Path:
    p = tmp_path / "caddy.lock"
    p.write_text(json.dumps(VALID_LOCK), encoding="utf-8")
    return p


def _argv(target_dir: Path, lockfile: Path, *extra: str) -> list[str]:
    return [
        "--target-dir", str(target_dir),
        "--lockfile", str(lockfile),
        *extra,
    ]


class TestExitCodes:

    def test_happy_path_returns_0(self, tmp_path, lockfile_path, mocker):
        fetch_mock = mocker.patch(
            "caddy_fetch.cli.fetch_and_install", return_value=Path("fake"),
        )
        rc = cli_mod.main(
            _argv(tmp_path / "out", lockfile_path, "--platform", "linux-amd64")
        )
        assert rc == 0
        fetch_mock.assert_called_once()

    def test_integrity_error_returns_2(self, tmp_path, lockfile_path, mocker):
        mocker.patch(
            "caddy_fetch.cli.fetch_and_install",
            side_effect=IntegrityError("sha mismatch"),
        )
        rc = cli_mod.main(
            _argv(tmp_path / "out", lockfile_path, "--platform", "linux-amd64")
        )
        assert rc == 2

    def test_gpg_error_returns_3(self, tmp_path, lockfile_path, mocker):
        """``GpgVerificationError`` raised from inside ``fetch_and_install``
        (the new call-site; verification now runs pre-extract against the
        archive) must surface as exit code 3."""
        mocker.patch(
            "caddy_fetch.cli.fetch_and_install",
            side_effect=GpgVerificationError("bad sig"),
        )
        rc = cli_mod.main(
            _argv(
                tmp_path / "out", lockfile_path,
                "--platform", "linux-amd64", "--verify-gpg",
            )
        )
        assert rc == 3

    def test_url_error_returns_1(self, tmp_path, lockfile_path, mocker):
        mocker.patch(
            "caddy_fetch.cli.fetch_and_install",
            side_effect=urllib.error.URLError("network down"),
        )
        rc = cli_mod.main(
            _argv(tmp_path / "out", lockfile_path, "--platform", "linux-amd64")
        )
        assert rc == 1

    def test_os_error_returns_1(self, tmp_path, lockfile_path, mocker):
        mocker.patch(
            "caddy_fetch.cli.fetch_and_install",
            side_effect=OSError("disk full"),
        )
        rc = cli_mod.main(
            _argv(tmp_path / "out", lockfile_path, "--platform", "linux-amd64")
        )
        assert rc == 1

    def test_value_error_returns_1(self, tmp_path, lockfile_path, mocker):
        # Unknown platform triggers ValueError from build_url.
        rc = cli_mod.main(
            _argv(
                tmp_path / "out", lockfile_path,
                "--platform", "haiku-ppc",
            ),
        )
        assert rc == 1


class TestLockfileLoadFailures:

    def test_missing_lockfile_returns_1(self, tmp_path):
        rc = cli_mod.main([
            "--target-dir", str(tmp_path / "out"),
            "--lockfile", str(tmp_path / "does-not-exist.lock"),
            "--platform", "linux-amd64",
        ])
        assert rc == 1

    def test_malformed_json_lockfile_returns_1(self, tmp_path):
        bad = tmp_path / "caddy.lock"
        bad.write_text("{ not json ", encoding="utf-8")
        rc = cli_mod.main([
            "--target-dir", str(tmp_path / "out"),
            "--lockfile", str(bad),
            "--platform", "linux-amd64",
        ])
        assert rc == 1


class TestMultiArch:

    def test_multi_arch_iterates_each_platform(
        self, tmp_path, lockfile_path, mocker,
    ):
        fetch_mock = mocker.patch(
            "caddy_fetch.cli.fetch_and_install", return_value=Path("x"),
        )
        target = tmp_path / "out"
        rc = cli_mod.main(_argv(
            target, lockfile_path,
            "--multi-arch", "linux-amd64,linux-arm64",
        ))
        assert rc == 0
        # Two invocations, one per platform.
        assert fetch_mock.call_count == 2
        # Each call targets a per-platform subdir.
        call_urls = [c.args[0] for c in fetch_mock.call_args_list]
        call_targets = [c.args[2] for c in fetch_mock.call_args_list]
        assert any("linux_amd64" in u for u in call_urls)
        assert any("linux_arm64" in u for u in call_urls)
        assert any(str(target / "linux-amd64") in str(t) for t in call_targets)
        assert any(str(target / "linux-arm64") in str(t) for t in call_targets)

    def test_multi_arch_trims_whitespace_and_skips_blanks(
        self, tmp_path, lockfile_path, mocker,
    ):
        fetch_mock = mocker.patch(
            "caddy_fetch.cli.fetch_and_install", return_value=Path("x"),
        )
        rc = cli_mod.main(_argv(
            tmp_path / "out", lockfile_path,
            "--multi-arch", "linux-amd64, ,linux-arm64",
        ))
        assert rc == 0
        assert fetch_mock.call_count == 2

    def test_multi_arch_empty_list_returns_1(
        self, tmp_path, lockfile_path, mocker,
    ):
        rc = cli_mod.main(_argv(
            tmp_path / "out", lockfile_path,
            "--multi-arch", " , , ",
        ))
        assert rc == 1

    def test_multi_arch_picks_correct_binary_name_for_windows(
        self, tmp_path, lockfile_path, mocker,
    ):
        fetch_mock = mocker.patch(
            "caddy_fetch.cli.fetch_and_install", return_value=Path("x"),
        )
        cli_mod.main(_argv(
            tmp_path / "out", lockfile_path,
            "--multi-arch", "windows-amd64,linux-amd64",
        ))
        call_targets = [str(c.args[2]) for c in fetch_mock.call_args_list]
        # Windows target should end with caddy.exe, Linux with caddy.
        assert any(t.endswith("caddy.exe") for t in call_targets)
        assert any(
            t.endswith("caddy") and not t.endswith("caddy.exe")
            for t in call_targets
        )


class TestAutoDetectHost:

    def test_no_platform_flag_uses_auto_detect(
        self, tmp_path, lockfile_path, mocker,
    ):
        mocker.patch(
            "caddy_fetch.cli.detect_host_platform",
            return_value="linux-amd64",
        )
        fetch_mock = mocker.patch(
            "caddy_fetch.cli.fetch_and_install", return_value=Path("x"),
        )
        rc = cli_mod.main(_argv(tmp_path / "out", lockfile_path))
        assert rc == 0
        fetch_mock.assert_called_once()


class TestArgparseErrors:

    def test_missing_target_dir_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main(["--platform", "linux-amd64"])
        assert exc.value.code != 0

    def test_mutually_exclusive_platform_and_multi_arch(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main([
                "--target-dir", str(tmp_path),
                "--platform", "linux-amd64",
                "--multi-arch", "linux-amd64,linux-arm64",
            ])
        assert exc.value.code != 0

    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main(["--help"])
        assert exc.value.code == 0


class TestVerifyGpgFlag:
    """``--verify-gpg`` now propagates to ``fetch_and_install`` via the
    ``signature_url`` kwarg (empty string when the per-platform lockfile
    entry does not advertise a ``sig_url``). Verification itself runs
    inside ``fetch_and_install`` against the downloaded archive, not
    against ``target_binary``."""

    def test_signature_url_passed_when_flag_set(
        self, tmp_path, lockfile_path, mocker,
    ):
        fetch_mock = mocker.patch(
            "caddy_fetch.cli.fetch_and_install", return_value=Path("x"),
        )
        rc = cli_mod.main(_argv(
            tmp_path / "out", lockfile_path,
            "--platform", "linux-amd64", "--verify-gpg",
        ))
        assert rc == 0
        fetch_mock.assert_called_once()
        kwargs = fetch_mock.call_args.kwargs
        # ``signature_url`` is passed explicitly (empty string when the
        # lockfile has no ``sig_url`` — today's Caddy v2.8.4 state).
        assert "signature_url" in kwargs
        assert kwargs["signature_url"] is not None

    def test_signature_url_none_when_flag_absent(
        self, tmp_path, lockfile_path, mocker,
    ):
        fetch_mock = mocker.patch(
            "caddy_fetch.cli.fetch_and_install", return_value=Path("x"),
        )
        cli_mod.main(_argv(
            tmp_path / "out", lockfile_path,
            "--platform", "linux-amd64",
        ))
        fetch_mock.assert_called_once()
        kwargs = fetch_mock.call_args.kwargs
        # No --verify-gpg → signature_url defaults to None.
        assert kwargs.get("signature_url") is None
