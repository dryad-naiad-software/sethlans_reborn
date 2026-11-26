# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release-list lookups, the page parser and the cache (BS-REL, #321, #98).

Three sources A, B, C (env setting); every request goes to a fake session.
The cache file is a per-test path under ``tmp_path``.
"""
import json
import logging

import pytest

from shared import file_lock, settings_file_io
from shared.blender_sources import (
    AllSourcesFailedError, BuildEntry, BuildNotPublishedError, find_build, latest_patch, list_series,
)
from shared.blender_sources import release_cache
from shared.blender_sources.listing import parse_build_links
from shared.blender_sources.release_list import LISTING_TIMEOUT, MAX_LISTING_BYTES
from tests.unit.shared._blender_sources_fakes import (
    A, B, C, FILE, FakeResponse, FakeSession, streamed_identity,
)

SHA_LINUX = "ab" * 32
SHA_WINDOWS = "cd" * 32
WINDOWS_ZIP = "blender-4.5.8-windows-x64.zip"


def _page(*names):
    return FakeResponse(body="".join(f'<a href="{n}">{n}</a>' for n in names).encode())


SERIES_PAGE = (FILE, WINDOWS_ZIP, "blender-4.5.8-windows-x64.msi", "blender-4.5.7-linux-x64.tar.xz",
               "blender-4.5.8.sha256", "blender-4.5.8.md5")
CHECKSUMS = f"{SHA_LINUX.upper()}  {FILE}\n{SHA_WINDOWS}  {WINDOWS_ZIP}\nnot a checksum line\n".encode()


def _page_url(source):
    return f"{source}Blender4.5/"


def _sha_url(source):
    return f"{source}Blender4.5/blender-4.5.8.sha256"


def _full(source):
    return {_page_url(source): _page(*SERIES_PAGE), _sha_url(source): FakeResponse(body=CHECKSUMS)}


def _cached(cache_file, data):
    cache_file.write_text(json.dumps(data), encoding="utf-8")


def _value(sha=SHA_LINUX, source=A, file_name=FILE):
    return {"file": file_name, "sha256": sha, "source": source}


def test_first_full_answer_wins_and_is_cached(three_sources, tmp_path):
    """BS-REL-1, BS-REL-2, BS-REL-6, BS-REL-8, BS-SUM-4 (D11): A lists the build but its checksum file is
    404, so A has not answered; B's full answer wins and every platform of it is cached, in lower case,
    with no other version's link; each request carries the listing timeout (#113), streams and asks for
    an uncompressed body (BS-DL-9, #323)."""
    cache_file = tmp_path / "release_cache.json"
    session = FakeSession({_page_url(A): _page(*SERIES_PAGE), **_full(B)})

    found = find_build("4.5.8", "linux-x64", cache_file, session=session)

    assert found == BuildEntry("4.5.8", "linux-x64", FILE, SHA_LINUX, B)
    assert session.urls == [_page_url(A), _sha_url(A), _page_url(B), _sha_url(B)]
    assert {call[1] for call in session.calls} == {LISTING_TIMEOUT}
    assert all(streamed_identity(call) for call in session.calls)
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"4.5.8": {
        "linux-x64": _value(source=B), "windows-x64": _value(SHA_WINDOWS, B, WINDOWS_ZIP)}}


def test_every_source_refusing_leaves_no_cache_and_fetches_again(three_sources, tmp_path):
    """#321 reproduction: with every request answering 403, three calls make three fetch attempts and
    no cache file is written."""
    cache_file = tmp_path / "release_cache.json"
    session = FakeSession({url: FakeResponse(status=403) for s in (A, B, C) for url in _full(s)})

    for _ in range(3):
        with pytest.raises(AllSourcesFailedError):
            find_build("4.5.8", "linux-x64", cache_file, session=session)

    assert session.urls.count(_page_url(A)) == 3
    assert not cache_file.exists()


def test_a_cache_hit_sends_no_request(three_sources, tmp_path):
    """BS-REL-5: a complete entry for the version and platform is answered from the cache."""
    cache_file = tmp_path / "release_cache.json"
    _cached(cache_file, {"4.5.8": {"linux-x64": _value(source=C)}})
    session = FakeSession()

    assert find_build("4.5.8", "linux-x64", cache_file, session=session) == BuildEntry(
        "4.5.8", "linux-x64", FILE, SHA_LINUX, C)
    assert session.calls == []


@pytest.mark.parametrize("cache", [
    {"4.5.7": {"linux-x64": _value(file_name="blender-4.5.7-linux-x64.tar.xz")}},
    {"4.5.8": {"windows-x64": _value(file_name=WINDOWS_ZIP)}},
    {"4.5.8": {"linux-x64": {"file": FILE, "source": C}}},
], ids=["version-missing", "platform-missing", "checksum-missing"])
def test_an_incomplete_cache_fetches_again(three_sources, tmp_path, cache):
    """BS-REL-5, #321: a cache without the version, the platform or the checksum is a miss."""
    cache_file = tmp_path / "release_cache.json"
    _cached(cache_file, cache)
    session = FakeSession(_full(A))

    assert find_build("4.5.8", "linux-x64", cache_file, session=session).sha256 == SHA_LINUX
    assert session.urls == [_page_url(A), _sha_url(A)]


def test_a_different_checksum_for_a_known_file_keeps_the_old_one(tmp_path, caplog):
    """BS-REL-6: a later answer cannot swap a cached file's SHA-256; the old one stays, with a WARNING
    naming both sources."""
    cache_file = tmp_path / "release_cache.json"
    _cached(cache_file, {"4.5.8": {"linux-x64": _value(source=A)}})
    newer = BuildEntry("4.5.8", "linux-x64", FILE, "ef" * 32, B)

    with caplog.at_level(logging.WARNING, logger=release_cache.__name__):
        release_cache.merge(cache_file, "4.5.8", {"linux-x64": newer})

    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"4.5.8": {"linux-x64": _value(source=A)}}
    [warning] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "b.test" in warning and "a.test" in warning


@pytest.mark.parametrize("read_error, writes", [
    (FileNotFoundError(2, "No such file"), True),
    (PermissionError(13, "Access is denied"), False),
], ids=["missing-starts-empty", "unreadable-left-alone"])
def test_merge_starts_empty_only_when_the_cache_file_is_missing(tmp_path, mocker, caplog, read_error, writes):
    """#321, BS-REL-6: a missing cache file starts from empty; one that exists but cannot be read is a
    WARNING and is not overwritten (it may hold other versions' checksums)."""
    cache_file = tmp_path / "release_cache.json"
    mocker.patch.object(release_cache, "_read_bytes", side_effect=read_error)
    replace = mocker.patch.object(settings_file_io, "atomic_replace")

    with caplog.at_level(logging.WARNING, logger=release_cache.__name__):
        release_cache.merge(cache_file, "4.5.8", {"linux-x64": BuildEntry("4.5.8", "linux-x64", FILE, SHA_LINUX, B)})

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    if writes:
        [(path, data)] = [call.args for call in replace.call_args_list]
        assert path == cache_file
        assert json.loads(data) == {"4.5.8": {"linux-x64": _value(source=B)}}
        assert warnings == []
    else:
        replace.assert_not_called()
        assert len(warnings) == 1 and "not updating it" in warnings[0]


@pytest.mark.parametrize("make_response", [
    lambda: FakeResponse(headers={"Content-Length": str(MAX_LISTING_BYTES + 1)}),
    lambda: FakeResponse(chunks=[b"x" * MAX_LISTING_BYTES, b"x"]),
], ids=["content-length-over-cap", "bytes-past-cap"])
def test_a_listing_body_over_the_cap_fails_the_source(three_sources, make_response):
    """BS-REL-1: a listing page larger than ``MAX_LISTING_BYTES`` (declared, or counted while read) fails
    that source with "too large"; the response is closed."""
    response = make_response()
    session = FakeSession({A: response})

    with pytest.raises(AllSourcesFailedError) as info:
        list_series(session=session)

    assert info.value.failures[0] == (A, "too large")
    assert response.closed


def test_a_lock_timeout_on_write_still_returns_the_answer(three_sources, tmp_path, mocker, caplog):
    """BS-REL-6: a cache write that cannot take its lock is a WARNING; the lookup still answers."""
    cache_file = tmp_path / "release_cache.json"
    mocker.patch.object(file_lock, "file_lock", side_effect=file_lock.FileLockTimeoutError("busy"))

    with caplog.at_level(logging.WARNING, logger=release_cache.__name__):
        found = find_build("4.5.8", "linux-x64", cache_file, session=FakeSession(_full(A)))

    assert found.sha256 == SHA_LINUX
    assert not cache_file.exists()
    assert any("Could not write the Blender release cache" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("names", [
    ("blender-4.5.8-windows-x64.zip", "blender-4.5.8-windows-x64.msi", "blender-4.5.8-windows-x64.msix"),
    ("blender-4.5.8-windows-x64.msix", "blender-4.5.8-windows-x64.msi", "blender-4.5.8-windows-x64.zip"),
], ids=["zip-first", "zip-last"])
def test_parser_picks_the_zip_and_ignores_unknown_links(names):
    """BS-REL-7, BS-REL-6: the Windows ``.zip`` whatever the order; unknown platforms, wrong extensions
    and ``.md5`` are ignored; each version keeps its own links (#98)."""
    html = _page(*names, "blender-4.5.8-freebsd-x64.tar.xz", "blender-4.5.8-linux-x64.zip",
                 "blender-4.5.8.md5", "blender-4.5.7-windows-x64.zip").content

    assert parse_build_links(html) == {
        "4.5.8": {"windows-x64": "blender-4.5.8-windows-x64.zip"},
        "4.5.7": {"windows-x64": "blender-4.5.7-windows-x64.zip"},
    }


def test_not_published_when_every_source_answers_without_the_build(three_sources, tmp_path):
    """BS-REL-3: every series page answers and none lists linux-arm64: ``BuildNotPublishedError``, no
    checksum file asked for and nothing cached."""
    cache_file = tmp_path / "release_cache.json"
    session = FakeSession({url: resp for s in (A, B, C) for url, resp in _full(s).items()})

    with pytest.raises(BuildNotPublishedError, match="Blender 4.5.8 has no linux-arm64 build") as info:
        find_build("4.5.8", "linux-arm64", cache_file, session=session)

    assert info.value.failures == [(A, "not listed"), (B, "not listed"), (C, "not listed")]
    assert session.urls == [_page_url(A), _page_url(B), _page_url(C)]
    assert not cache_file.exists()


def test_list_series_takes_the_first_source_with_a_series_from_4(three_sources):
    """BS-REL-1: an index with only pre-4 series has not answered; the next source's 4.x series win,
    oldest first by integer order."""
    session = FakeSession({
        A: _page("Blender3.6/"),
        B: _page("Blender3.6/", "Blender4.10/", "Blender4.2/", "Blender5.0/", "blender-4.2.0.zip"),
    })

    assert list_series(session=session) == ["4.2", "4.10", "5.0"]
    assert session.urls == [A, B]


def test_latest_patch_is_the_highest_by_integer_tuple(three_sources):
    """BS-REL-1, BS-REL-10: 4.5.10 is newer than 4.5.9 (an integer, not a text, comparison)."""
    session = FakeSession({_page_url(A): _page("blender-4.5.9-linux-x64.tar.xz", "blender-4.5.10-linux-x64.tar.xz")})

    assert latest_patch("4.5", session=session) == "4.5.10"
