# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline tests for the download side of ``tools/check_ffmpeg_pins.py``.

HTTPS-only redirects, the final-URL check, error mapping and the
streaming hash.  ``urllib.request.build_opener`` is replaced by a fake
opener; no test opens a socket.
"""

from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from ._ffmpeg_pins_helpers import FakeOpener, FakeResponse, load_tool

URL = "https://example.invalid/f.zip"


@pytest.fixture
def tool():
    return load_tool()


@pytest.fixture
def opener(tool, mocker):
    fake = FakeOpener()
    build = mocker.patch.object(tool.urllib.request, "build_opener", return_value=fake)
    fake.build = build
    return fake


class TestHttpsOnlyRedirectHandler:
    def _redirect(self, tool, newurl, code=302):
        handler = tool._HttpsOnlyRedirectHandler()
        request = urllib.request.Request(URL)
        return handler.redirect_request(request, None, code, "Found", {}, newurl)

    @pytest.mark.parametrize("newurl", [
        "http://objects.example.invalid/f.zip",
        "ftp://objects.example.invalid/f.zip",
        "file:///etc/passwd",
    ], ids=["http", "ftp", "file"])
    def test_refuses_non_https_redirect(self, tool, newurl):
        with pytest.raises(tool.CheckError, match="refused redirect to non-HTTPS URL"):
            self._redirect(tool, newurl)

    @pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
    def test_follows_https_redirect(self, tool, code):
        new = self._redirect(tool, "https://objects.example.invalid/f.zip", code)
        assert isinstance(new, urllib.request.Request)
        assert new.full_url == "https://objects.example.invalid/f.zip"

    def test_refused_redirect_closes_the_response_fp(self, tool):
        handler = tool._HttpsOnlyRedirectHandler()
        fp = io.BytesIO(b"redirect body")
        with pytest.raises(tool.CheckError):
            handler.redirect_request(
                urllib.request.Request(URL), fp, 302, "Found", {},
                "http://objects.example.invalid/f.zip",
            )
        assert fp.closed is True

    def test_refused_redirect_without_fp_still_refuses(self, tool):
        with pytest.raises(tool.CheckError):
            self._redirect(tool, "http://objects.example.invalid/f.zip")

    def test_scheme_check_ignores_case(self, tool):
        new = self._redirect(tool, "HTTPS://objects.example.invalid/f.zip")
        assert new is not None

    def test_opener_uses_the_https_only_handler(self, tool, opener):
        opener.response = FakeResponse([])
        tool._open(URL)
        (handler,), _ = opener.build.call_args
        assert isinstance(handler, tool._HttpsOnlyRedirectHandler)


class TestOpen:
    def test_sends_user_agent_and_timeout(self, tool, opener):
        opener.response = FakeResponse([])
        assert tool._open(URL) is opener.response
        request, timeout = opener.requests[0]
        assert request.full_url == URL
        assert request.get_header("User-agent") == tool.USER_AGENT
        assert timeout == tool.SOCKET_TIMEOUT_S

    def test_http_error_maps_to_check_error(self, tool, opener):
        opener.error = urllib.error.HTTPError(URL, 404, "Not Found", {}, None)
        with pytest.raises(tool.CheckError, match="HTTP 404 Not Found"):
            tool._open(URL)

    def test_http_error_is_closed_before_raising(self, tool, opener, mocker):
        body = io.BytesIO(b"<html>not found</html>")
        error = urllib.error.HTTPError(URL, 404, "Not Found", {}, body)
        close_spy = mocker.spy(error, "close")
        opener.error = error
        with pytest.raises(tool.CheckError):
            tool._open(URL)
        close_spy.assert_called_once_with()
        assert body.closed is True

    def test_url_error_maps_to_check_error(self, tool, opener):
        opener.error = urllib.error.URLError("name resolution failed")
        with pytest.raises(tool.CheckError, match="connection failed: name resolution failed"):
            tool._open(URL)

    def test_os_error_maps_to_check_error(self, tool, opener):
        opener.error = TimeoutError("timed out")
        with pytest.raises(tool.CheckError, match="connection failed: timed out"):
            tool._open(URL)

    def test_non_https_final_url_is_refused_and_closed(self, tool, opener):
        opener.response = FakeResponse([], url="http://example.invalid/f.zip")
        with pytest.raises(tool.CheckError, match="final URL is not HTTPS"):
            tool._open(URL)
        assert opener.response.closed is True


class TestSha256OfUrl:
    def test_refuses_non_https_url_without_opening(self, tool, mocker):
        spy = mocker.patch.object(tool, "_open")
        with pytest.raises(tool.CheckError, match="refused non-HTTPS URL"):
            tool.sha256_of_url("http://example.invalid/f.zip")
        spy.assert_not_called()

    def test_hashes_every_chunk(self, tool, mocker):
        response = FakeResponse([b"abc", b"def"], length=6)
        mocker.patch.object(tool, "_open", return_value=response)
        digest, size = tool.sha256_of_url(URL)
        assert digest == hashlib.sha256(b"abcdef").hexdigest()
        assert size == 6
        assert response.read_sizes[0] == tool.CHUNK_BYTES
        assert response.closed is True

    def test_missing_content_length_is_fine(self, tool, mocker):
        mocker.patch.object(tool, "_open", return_value=FakeResponse([b"xyz"]))
        assert tool.sha256_of_url(URL) == (hashlib.sha256(b"xyz").hexdigest(), 3)

    def test_short_read_is_an_error(self, tool, mocker):
        mocker.patch.object(tool, "_open", return_value=FakeResponse([b"abc"], length=10))
        with pytest.raises(tool.CheckError, match="short read: got 3 of 10 bytes"):
            tool.sha256_of_url(URL)

    def test_content_length_over_cap_is_an_error(self, tool, mocker):
        mocker.patch.object(tool, "MAX_BYTES", 5)
        response = FakeResponse([b"abcdef"], length=6)
        mocker.patch.object(tool, "_open", return_value=response)
        with pytest.raises(tool.CheckError, match="Content-Length 6 exceeds cap"):
            tool.sha256_of_url(URL)
        assert response.read_sizes == []

    def test_body_over_cap_is_an_error(self, tool, mocker):
        mocker.patch.object(tool, "MAX_BYTES", 5)
        mocker.patch.object(tool, "_open", return_value=FakeResponse([b"abc", b"def"]))
        with pytest.raises(tool.CheckError, match="body exceeds cap of 5 bytes"):
            tool.sha256_of_url(URL)

    def test_read_error_is_an_error(self, tool, mocker):
        response = FakeResponse([b"abc"])
        response.read = mocker.Mock(side_effect=ConnectionResetError("reset"))
        mocker.patch.object(tool, "_open", return_value=response)
        with pytest.raises(tool.CheckError, match="read failed after 0 bytes"):
            tool.sha256_of_url(URL)

    def test_deadline_is_enforced(self, tool, mocker):
        clock = iter([0.0, tool.DOWNLOAD_DEADLINE_S + 1.0])
        mocker.patch.object(tool, "time", SimpleNamespace(monotonic=lambda: next(clock)))
        mocker.patch.object(tool, "_open", return_value=FakeResponse([b"abc"]))
        with pytest.raises(tool.CheckError, match="download exceeded"):
            tool.sha256_of_url(URL)

    def test_check_url_reports_a_refused_redirect_as_error(self, tool, opener, capsys):
        """A redirect refusal inside the opener surfaces as an ERROR line."""
        opener.error = tool.CheckError("refused redirect to non-HTTPS URL: http://x/")
        assert tool.check_url(URL, ["p"], {"p": "a" * 64}) is False
        assert "ERROR    [p]" in capsys.readouterr().out
