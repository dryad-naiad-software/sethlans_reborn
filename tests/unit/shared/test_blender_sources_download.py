# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``download_build``: fallback over the sources, checksum and partial files (BS-DL, BS-SUM, BS-LOG, #320, #323).

Three sources A, B, C (env setting); every request goes to a fake session.
"""
import logging
import socket
import threading
from unittest import mock

import pytest
import requests

from shared.blender_sources import (
    AllSourcesFailedError, BlenderSourcesError, DownloadStoppedError, download_build, sources,
)
from shared.blender_sources import download as download_module
from tests.unit.shared._blender_sources_fakes import (
    A, B, BODY, C, FILE, SHA, FakeResponse, FakeSession, build_url, entry, redirect, streamed_identity,
)

LOGGER = download_module.__name__


def _good():
    return FakeResponse(body=BODY)


def _leftovers(folder):
    return sorted(p.name for p in folder.iterdir())


def test_last_worked_first_then_list_order_fresh_each_call(three_sources, tmp_path):
    """BS-DL-1, BS-DL-2, BS-DL-6: a verified download makes its source the first of the next call; the
    order is rebuilt per call, each source is tried once and the call ends after the last."""
    first = FakeSession({build_url(A): FakeResponse(status=403), build_url(B): _good()})
    download_build(entry(), tmp_path, session=first)
    assert first.urls == [build_url(A), build_url(B)]
    assert sources.last_worked() == B

    second = FakeSession({src: FakeResponse(status=403) for src in map(build_url, (A, B, C))})
    with pytest.raises(AllSourcesFailedError):
        download_build(entry(), tmp_path, session=second)
    assert second.urls == [build_url(B), build_url(A), build_url(C)]
    assert sources.last_worked() == B  # only a checksum match sets it


def _stalled():
    """A body whose first chunk arrives after the whole stall budget has passed."""
    def chunks():
        download_module.time.monotonic.return_value += download_module.STALL_BUDGET_SECONDS + 1
        yield BODY
    return FakeResponse(chunks=chunks())


_FAILURES = {
    "refused": (lambda: requests.exceptions.ConnectionError(ConnectionRefusedError(10061, "refused")),
                "connection refused"),
    "dns": (lambda: requests.exceptions.ConnectionError(socket.gaierror(11001, "getaddrinfo failed")),
            "DNS failure"),
    "tls": (lambda: requests.exceptions.SSLError("certificate verify failed"), "TLS error"),
    "http-403": (lambda: FakeResponse(status=403), "HTTP 403"),
    "http-500": (lambda: FakeResponse(status=500), "HTTP 500"),
    "read-timeout": (lambda: requests.exceptions.ReadTimeout("slow"), "read timeout 120 s"),
    "stall-budget": (_stalled, "stall budget 1800 s"),
    # BS-SRC-4: the http:// target is refused before it is requested (the URL list below shows it never is).
    "http-hop-in-redirect": (lambda: redirect(302, "http://middle.test/b.tar.xz"), "redirected to non-HTTPS"),
    "checksum-mismatch": (lambda: FakeResponse(body=b"tampered bytes"), "checksum mismatch"),
    "content-length-over-cap": (lambda: FakeResponse(
        body=BODY, headers={"Content-Length": str(len(BODY) + 1)}), "too large"),
    "bytes-past-cap": (lambda: FakeResponse(chunks=[BODY, b"x"]), "too large"),
    # #323: a small compressed body can unpack past every byte cap, so any Content-Encoding fails.
    "gzip": (lambda: FakeResponse(body=BODY, headers={"Content-Encoding": "gzip"}), "compressed response"),
    "gzip-twice": (lambda: FakeResponse(body=BODY, headers={"Content-Encoding": "gzip, gzip"}),
                   "compressed response"),
    "gzip-padded-upper-case": (lambda: FakeResponse(body=BODY, headers={"Content-Encoding": " GZIP "}),
                               "compressed response"),
}


@pytest.mark.parametrize("failure", list(_FAILURES))
def test_each_failure_moves_to_the_next_source_and_leaves_no_part(
        three_sources, tmp_path, mocker, caplog, failure):
    """BS-DL-3, BS-SRC-4, BS-SUM-2, BS-DL-9, BS-DL-5, BS-LOG-1, #320, #323: the failed source is logged with
    its reason, the next source is tried against the same checksum, and only the verified archive remains.
    Every request streams and asks for ``Accept-Encoding: identity`` (#323)."""
    mocker.patch.object(download_module, "time", monotonic=mock.Mock(return_value=0.0))
    answer, reason = _FAILURES[failure]
    session = FakeSession({build_url(A): answer(), build_url(B): _good()})

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        path = download_build(entry(), tmp_path, session=session, max_bytes=len(BODY))

    assert session.urls == [build_url(A), build_url(B)]
    assert all(streamed_identity(call) for call in session.calls)
    assert path == tmp_path / FILE and path.read_bytes() == BODY
    assert _leftovers(tmp_path) == [FILE]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [
        f"Blender 4.5.8 linux-x64: build from a.test failed: {reason}"]


def test_a_local_write_error_ends_the_call(three_sources, tmp_path, mocker):
    """BS-DL-3: an ``OSError`` while writing is not the source's fault: no other source is tried."""
    handle = mock.MagicMock()
    handle.__enter__.return_value.write.side_effect = OSError(28, "No space left on device")
    mocker.patch.object(download_module, "open", create=True, return_value=handle)
    session = FakeSession({src: _good() for src in map(build_url, (A, B, C))})

    with pytest.raises(OSError, match="No space left"):
        download_build(entry(), tmp_path, session=session)

    assert session.urls == [build_url(A)]


def test_a_stop_mid_stream_ends_the_call_and_leaves_no_part(three_sources, tmp_path):
    """BS-DL-10, BS-DL-5: ``stop`` set while the body streams raises ``DownloadStoppedError``, deletes the
    ``.part`` and tries no other source."""
    stop = threading.Event()

    def chunks():
        yield BODY[:5]
        stop.set()  # the setup page's cancel, between two chunks
        yield BODY[5:]
    session = FakeSession({build_url(A): FakeResponse(chunks=chunks()), build_url(B): _good()})

    with pytest.raises(DownloadStoppedError):
        download_build(entry(), tmp_path, session=session, stop=stop)

    assert session.urls == [build_url(A)]
    assert _leftovers(tmp_path) == []


def test_all_sources_failing_is_one_error_naming_each(three_sources, tmp_path):
    """BS-LOG-2: one ``AllSourcesFailedError`` with every source and its reason, in order, on one line."""
    session = FakeSession({
        build_url(A): FakeResponse(status=403),
        build_url(B): requests.exceptions.ConnectionError(ConnectionRefusedError(10061, "refused")),
        build_url(C): requests.exceptions.ReadTimeout("slow"),
    })

    with pytest.raises(AllSourcesFailedError) as info:
        download_build(entry(), tmp_path, session=session)

    assert info.value.failures == [(A, "HTTP 403"), (B, "connection refused"), (C, "read timeout 120 s")]
    assert str(info.value) == ("Blender 4.5.8 linux-x64: every source failed: a.test: HTTP 403; "
                               "b.test: connection refused; c.test: read timeout 120 s")
    assert _leftovers(tmp_path) == []


def test_checksum_from_the_list_source_build_from_another(three_sources, tmp_path, caplog):
    """BS-SUM-1, BS-LOG-3 (D4): the list's source refuses the build, a mirror serves it; the list's
    checksum is accepted and the success line names both hosts."""
    session = FakeSession({build_url(A): FakeResponse(status=403), build_url(B): _good()})

    with caplog.at_level(logging.INFO, logger=LOGGER):
        path = download_build(entry(source=A), tmp_path, session=session)

    assert path.read_bytes() == BODY
    assert "Blender 4.5.8 linux-x64: downloaded from b.test; checksum from a.test." in [
        r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


@pytest.mark.parametrize("sha256", ["", SHA[:-1]], ids=["none", "63-hex"])
def test_no_checksum_is_refused_before_any_request(three_sources, tmp_path, sha256):
    """BS-SUM-3 (D4): an entry without a 64-hex SHA-256 sends nothing."""
    session = FakeSession({build_url(A): _good()})

    with pytest.raises(BlenderSourcesError, match=f"no SHA-256 published for {FILE}"):
        download_build(entry(sha256=sha256), tmp_path, session=session)

    assert session.calls == []
