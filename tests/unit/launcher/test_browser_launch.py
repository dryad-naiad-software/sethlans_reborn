# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher/browser_launch.py`` (FR-13 / FR-14)."""

from __future__ import annotations

from launcher import browser_launch
from launcher.browser_launch import open_browser, print_setup_banner


# ------------------------------------------------------------------
# open_browser — URL never carries ?token=
# ------------------------------------------------------------------

class TestOpenBrowserUrlNoQueryString:

    def test_url_has_no_query_string(self, mocker):
        spy = mocker.patch.object(browser_launch.webbrowser, "open")
        # Force interactive path.
        mocker.patch.object(
            browser_launch, "is_headless", return_value=False,
        )
        open_browser(
            port=8080, no_browser=False, print_url=False,
            path="/setup/", setup_token="TOKEN_VALUE",
        )
        spy.assert_called_once()
        url = spy.call_args.args[0]
        assert url == "https://localhost:8080/setup/"
        assert "?token=" not in url
        assert "TOKEN_VALUE" not in url

    def test_setup_token_kwarg_is_ignored(self, mocker):
        spy = mocker.patch.object(browser_launch.webbrowser, "open")
        mocker.patch.object(
            browser_launch, "is_headless", return_value=False,
        )
        # Pass token — the url must still not include it.
        open_browser(
            8080, False, False, "/setup/",
            setup_token="aaaabbbbcccc",
        )
        url = spy.call_args.args[0]
        assert "aaaabbbbcccc" not in url

    def test_no_browser_skips_open(self, mocker):
        spy = mocker.patch.object(browser_launch.webbrowser, "open")
        mocker.patch.object(
            browser_launch, "is_headless", return_value=False,
        )
        open_browser(8080, no_browser=True, print_url=False, path="/")
        spy.assert_not_called()

    def test_headless_skips_open(self, mocker):
        spy = mocker.patch.object(browser_launch.webbrowser, "open")
        mocker.patch.object(
            browser_launch, "is_headless", return_value=True,
        )
        open_browser(8080, False, False, "/")
        spy.assert_not_called()


# ------------------------------------------------------------------
# print_setup_banner — FR-14 exact format
# ------------------------------------------------------------------

class TestPrintSetupBannerSuccess:

    def test_returns_true_when_clipboard_copy_succeeds(
        self, mocker, capsys, tmp_path,
    ):
        mocker.patch.object(
            browser_launch, "copy_token_to_clipboard", return_value=True,
        )
        result = print_setup_banner(
            port=8080, wizard_path="/setup/",
            setup_token="TOKENVALUE", data_dir=tmp_path,
        )
        out = capsys.readouterr().out
        assert result is True
        assert "Sethlans Setup" in out
        assert "https://localhost:8080/setup/" in out
        assert "Token:  TOKENVALUE" in out
        assert "(Token copied to clipboard)" in out
        assert "(Copy the token above manually.)" not in out

    def test_banner_boundary_lines_are_equals_signs(
        self, mocker, capsys, tmp_path,
    ):
        mocker.patch.object(
            browser_launch, "copy_token_to_clipboard", return_value=True,
        )
        print_setup_banner(
            port=8080, wizard_path="/setup/",
            setup_token="TKN", data_dir=tmp_path,
        )
        out = capsys.readouterr().out.splitlines()
        # First and last stdout lines of the banner are "=" bars.
        bar_lines = [ln for ln in out if set(ln) == {"="}]
        assert len(bar_lines) >= 2


class TestPrintSetupBannerFailure:

    def test_returns_false_when_clipboard_copy_fails(
        self, mocker, capsys, tmp_path,
    ):
        mocker.patch.object(
            browser_launch, "copy_token_to_clipboard", return_value=False,
        )
        result = print_setup_banner(
            port=8080, wizard_path="/setup/",
            setup_token="T", data_dir=tmp_path,
        )
        out = capsys.readouterr().out
        assert result is False
        assert "(Copy the token above manually.)" in out
        assert "(Token copied to clipboard)" not in out

    def test_clipboard_exception_treated_as_failure(
        self, mocker, capsys, tmp_path,
    ):
        # Helper contract: never raises.  Banner still handles defensively.
        mocker.patch.object(
            browser_launch, "copy_token_to_clipboard",
            side_effect=RuntimeError("unexpected"),
        )
        result = print_setup_banner(
            port=8080, wizard_path="/setup/",
            setup_token="T", data_dir=tmp_path,
        )
        assert result is False
        out = capsys.readouterr().out
        assert "(Copy the token above manually.)" in out

    def test_copy_called_with_token(self, mocker, tmp_path):
        spy = mocker.patch.object(
            browser_launch, "copy_token_to_clipboard", return_value=True,
        )
        print_setup_banner(
            port=8080, wizard_path="/setup/",
            setup_token="THETOKEN", data_dir=tmp_path,
        )
        spy.assert_called_once_with("THETOKEN")
