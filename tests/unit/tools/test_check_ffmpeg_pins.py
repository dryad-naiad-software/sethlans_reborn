# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline tests for ``tools/check_ffmpeg_pins.py`` (issue #302).

Table checks, the OK / MISMATCH / ERROR lines and the exit codes.  The
download (``sha256_of_url``) is always patched; download internals are
in ``test_check_ffmpeg_pins_download.py``.
"""

from __future__ import annotations

import re

import pytest

from ._ffmpeg_pins_helpers import REPO_ROOT, load_tool

A = "a" * 64
B = "b" * 64
URL_X = "https://example.invalid/x.zip"
URL_Y = "https://example.invalid/y.tar.xz"


@pytest.fixture
def tool():
    return load_tool()


@pytest.fixture
def fake_pins(tool, mocker):
    """Replace the loaded pin tables with small fake ones."""
    tables = {
        "urls": {"p1": URL_X, "p2": URL_Y},
        "digests": {"p1": A, "p2": B},
    }
    mocker.patch.object(
        tool, "load_pins", side_effect=lambda: (tables["urls"], tables["digests"]),
    )
    return tables


@pytest.fixture
def downloads(tool, mocker):
    """Patch the download; map URL -> digest, or URL -> CheckError."""
    results = {URL_X: A, URL_Y: B}

    def fake(url):
        result = results[url]
        if isinstance(result, Exception):
            raise result
        return result, 123

    spy = mocker.patch.object(tool, "sha256_of_url", side_effect=fake)
    return {"results": results, "spy": spy}


class TestLoadPins:
    def test_loads_the_real_constants_by_path(self, tool):
        urls, digests = tool.load_pins()
        assert set(urls) == set(digests)
        assert "linux-x64" in urls

    def test_real_tables_have_no_problems(self, tool):
        assert tool.validate_tables(*tool.load_pins()) == []

    def test_missing_tables_raise(self, tool, tmp_path):
        bogus = tmp_path / "constants.py"
        bogus.write_text("FFMPEG_URLS = {}\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="missing"):
            tool.load_pins(bogus)


class TestTimeBudget:
    def test_timeouts(self, tool):
        assert tool.SOCKET_TIMEOUT_S == 30
        assert tool.DOWNLOAD_DEADLINE_S == 240

    def test_worst_case_fits_the_workflow_timeout(self, tool):
        """Each URL can take deadline + one socket timeout; all must fit."""
        workflow_path = REPO_ROOT / ".github" / "workflows" / "ffmpeg-pins.yml"
        if not workflow_path.is_file():
            # The Docker test image leaves out .github/ (.dockerignore).
            pytest.skip(f"workflow file not in this checkout: {workflow_path}")
        workflow = workflow_path.read_text(encoding="utf-8")
        minutes = int(re.search(r"timeout-minutes:\s*(\d+)", workflow).group(1))
        urls, _ = tool.load_pins()
        per_url = tool.DOWNLOAD_DEADLINE_S + tool.SOCKET_TIMEOUT_S
        assert len(set(urls.values())) * per_url < minutes * 60


class TestValidateTables:
    def test_clean_tables(self, tool):
        assert tool.validate_tables({"p": URL_X}, {"p": A}) == []

    def test_upper_case_hex_is_accepted(self, tool):
        assert tool.validate_tables({"p": URL_X}, {"p": A.upper()}) == []

    def test_key_only_in_urls(self, tool):
        problems = tool.validate_tables({"p": URL_X, "q": URL_Y}, {"p": A})
        assert problems == ["q: in FFMPEG_URLS but not in FFMPEG_SHA256"]

    def test_key_only_in_digests(self, tool):
        problems = tool.validate_tables({"p": URL_X}, {"p": A, "q": B})
        assert problems == ["q: in FFMPEG_SHA256 but not in FFMPEG_URLS"]

    @pytest.mark.parametrize("digest", ["", None, "PLACEHOLDER_FILL_AT_RELEASE"])
    def test_empty_or_placeholder_digest(self, tool, digest):
        problems = tool.validate_tables({"p": URL_X}, {"p": digest})
        assert problems == ["p: digest is empty or the placeholder"]

    @pytest.mark.parametrize("digest", ["g" * 64, "a" * 63, "a" * 65, 12345],
                             ids=["non_hex", "short", "long", "int"])
    def test_non_hex_digest(self, tool, digest):
        problems = tool.validate_tables({"p": URL_X}, {"p": digest})
        assert len(problems) == 1
        assert problems[0].startswith("p: digest is not 64 hex chars")

    @pytest.mark.parametrize("url", [
        "http://example.invalid/x.zip", "ftp://example.invalid/x", "x.zip", None,
    ], ids=["http", "ftp", "no_scheme", "none"])
    def test_non_https_url(self, tool, url):
        problems = tool.validate_tables({"p": url}, {"p": A})
        assert problems == [f"p: URL is not HTTPS: {url!r}"]


class TestGroupByUrl:
    def test_shared_url_groups_platform_ids(self, tool):
        groups = tool.group_by_url(
            {"m1": URL_X, "m2": URL_X, "l": URL_Y}, {"m1": A, "m2": A, "l": B}, None,
        )
        assert groups == {URL_X: ["m1", "m2"], URL_Y: ["l"]}

    def test_only_filters_to_one_platform(self, tool):
        groups = tool.group_by_url({"p": URL_X, "q": URL_Y}, {"p": A, "q": B}, "q")
        assert groups == {URL_Y: ["q"]}

    def test_invalid_digests_are_left_out(self, tool):
        groups = tool.group_by_url({"p": URL_X, "q": URL_Y}, {"p": "", "q": B}, None)
        assert groups == {URL_Y: ["q"]}

    @pytest.mark.parametrize("url", [None, 42, ["https://example.invalid/x"]],
                             ids=["none", "int", "list"])
    def test_non_string_urls_are_left_out(self, tool, url):
        groups = tool.group_by_url({"p": url, "q": URL_Y}, {"p": A, "q": B}, None)
        assert groups == {URL_Y: ["q"]}


class TestCheckUrl:
    def test_ok(self, tool, downloads, capsys):
        assert tool.check_url(URL_X, ["p1"], {"p1": A}) is True
        assert capsys.readouterr().out.startswith(f"OK       [p1] {URL_X}")

    def test_upper_case_pin_matches_lower_case_download(self, tool, downloads):
        assert tool.check_url(URL_X, ["p1"], {"p1": A.upper()}) is True

    def test_mismatch(self, tool, downloads, capsys):
        downloads["results"][URL_X] = B
        assert tool.check_url(URL_X, ["p1"], {"p1": A}) is False
        out = capsys.readouterr().out
        assert out.startswith(f"MISMATCH [p1] {URL_X}")
        assert f"expected={A}" in out
        assert f"actual=  {B}" in out

    def test_download_error(self, tool, downloads, capsys):
        downloads["results"][URL_X] = tool.CheckError("HTTP 404 Not Found")
        assert tool.check_url(URL_X, ["p1"], {"p1": A}) is False
        assert capsys.readouterr().out == f"ERROR    [p1] {URL_X}: HTTP 404 Not Found\n"

    def test_shared_url_with_different_digests_errors_without_download(
        self, tool, downloads, capsys,
    ):
        assert tool.check_url(URL_X, ["m1", "m2"], {"m1": A, "m2": B}) is False
        out = capsys.readouterr().out
        assert out.startswith(f"ERROR    [m1, m2] {URL_X}: platform ids sharing")
        downloads["spy"].assert_not_called()

    def test_shared_url_digests_differing_only_in_case_are_one_pin(self, tool, downloads):
        assert tool.check_url(URL_X, ["m1", "m2"], {"m1": A, "m2": A.upper()}) is True
        downloads["spy"].assert_called_once_with(URL_X)


class TestMain:
    def test_all_ok_exits_0(self, tool, fake_pins, downloads, capsys):
        assert tool.main([]) == 0
        assert capsys.readouterr().out.endswith("All FFmpeg pins verified.\n")
        assert downloads["spy"].call_count == 2

    def test_mismatch_exits_1_and_still_checks_the_rest(
        self, tool, fake_pins, downloads, capsys,
    ):
        downloads["results"][URL_X] = B
        assert tool.main([]) == 1
        out = capsys.readouterr().out
        assert "MISMATCH [p1]" in out
        assert "OK       [p2]" in out
        assert out.endswith("FFmpeg pin check FAILED.\n")

    def test_download_error_exits_1(self, tool, fake_pins, downloads):
        downloads["results"][URL_Y] = tool.CheckError("connection failed: x")
        assert tool.main([]) == 1

    def test_table_problem_exits_1(self, tool, fake_pins, downloads, capsys):
        fake_pins["digests"]["p1"] = ""
        assert tool.main([]) == 1
        out = capsys.readouterr().out
        assert "ERROR    p1: digest is empty or the placeholder" in out
        downloads["spy"].assert_called_once_with(URL_Y)

    def test_none_url_is_reported_and_not_downloaded(
        self, tool, fake_pins, downloads, capsys,
    ):
        fake_pins["urls"]["p1"] = None
        assert tool.main([]) == 1
        assert "ERROR    p1: URL is not HTTPS: None" in capsys.readouterr().out
        downloads["spy"].assert_called_once_with(URL_Y)

    def test_only_checks_one_platform(self, tool, fake_pins, downloads):
        assert tool.main(["--only", "p2"]) == 0
        downloads["spy"].assert_called_once_with(URL_Y)

    def test_unknown_only_exits_1_without_download(
        self, tool, fake_pins, downloads, capsys,
    ):
        assert tool.main(["--only", "nope"]) == 1
        assert "unknown platform id 'nope'; known: p1, p2" in capsys.readouterr().out
        downloads["spy"].assert_not_called()

    def test_no_checkable_urls_exits_1(self, tool, fake_pins, downloads, capsys):
        fake_pins["digests"].update({"p1": "", "p2": "zz"})
        assert tool.main([]) == 1
        assert "ERROR    no URLs to check" in capsys.readouterr().out
        downloads["spy"].assert_not_called()

    def test_load_failure_exits_1(self, tool, mocker, capsys):
        mocker.patch.object(tool, "load_pins", side_effect=RuntimeError("boom"))
        assert tool.main([]) == 1
        assert capsys.readouterr().out == "ERROR    cannot load pins: boom\n"

    def test_real_pins_with_matching_downloads_exit_0(self, tool, mocker):
        urls, digests = tool.load_pins()
        by_url = {urls[pid]: digests[pid] for pid in urls}
        spy = mocker.patch.object(
            tool, "sha256_of_url", side_effect=lambda url: (by_url[url], 1),
        )
        assert tool.main([]) == 0
        assert spy.call_count == len(set(urls.values()))
