# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source setting, session check, checked GET, version and platform tables (BS-CFG, BS-SRC, BS-DL-7, BS-REL-7).

``sethlans.ini`` is replaced by a parser handed out by a mocked
``settings_file.read_settings``; requests go to a fake transport.
"""
import configparser

import pytest

from shared import settings_file
from shared.blender_sources import (
    DEFAULT_SOURCES, SOURCES_ENV, BlenderSourcesConfigError, BlenderSourcesError, configured_sources,
    current_platform_id, download_build, is_version, list_series, sources,
)
from shared.blender_sources.errors import SourceFailure
from shared.blender_sources.sources import MAX_REDIRECTS
from tests.unit.shared._blender_sources_fakes import FakeResponse, FakeSession, entry, redirect, streamed_identity

FILE_SOURCE = "https://file.test/blender/release/"


@pytest.fixture
def settings(monkeypatch):
    """No env value; ``read_settings`` returns the parser the test fills (empty: no setting)."""
    monkeypatch.delenv(SOURCES_ENV, raising=False)
    parser = configparser.RawConfigParser()
    monkeypatch.setattr(settings_file, "read_settings", lambda *a, **kw: parser)
    return parser


def _set_file(parser, value):
    parser.add_section("blender")
    parser.set("blender", "sources", value)


@pytest.mark.parametrize("env_value, file_value, expected", [
    ("https://Env.test/a, https://env.test/a/,https://env2.test/b", FILE_SOURCE,
     ["https://env.test/a/", "https://env2.test/b/"]),
    (None, FILE_SOURCE, [FILE_SOURCE]),
    (None, None, list(DEFAULT_SOURCES)),
], ids=["env-beats-file", "file-beats-default", "default"])
def test_env_beats_file_beats_default(monkeypatch, settings, env_value, file_value, expected):
    """BS-CFG-1: env, else ``[blender] sources``, else the default; a set value replaces the list.
    BS-SRC-3: a missing ``/`` is added and a repeated entry dropped (first kept)."""
    if env_value is not None:
        monkeypatch.setenv(SOURCES_ENV, env_value)
    if file_value is not None:
        _set_file(settings, file_value)

    assert configured_sources() == expected


def _unreadable(*_args, **_kwargs):
    raise settings_file.SettingsFileError("Cannot read sethlans.ini: PermissionError")


@pytest.mark.parametrize("env_value, unreadable, verify, call, error, message", [
    ("https://ok.test/r/,http://mirror.example.lan/", False, True, "download",
     BlenderSourcesConfigError, f"{SOURCES_ENV} entry 2 must use https"),
    ("https://user@mirror.test/r/", False, True, "download",
     BlenderSourcesConfigError, f"{SOURCES_ENV} entry 1 must not contain user info"),
    ("  ", False, True, "download", BlenderSourcesConfigError, f"{SOURCES_ENV} is set but lists no source"),
    (None, True, True, "download", BlenderSourcesConfigError, "cannot read the Blender sources"),
    (None, False, False, "download", BlenderSourcesError, "certificate checks turned off"),
    (None, False, False, "listing", BlenderSourcesError, "certificate checks turned off"),
    (None, False, None, "download", BlenderSourcesError, "certificate checks turned off"),
    (None, False, 0, "download", BlenderSourcesError, "certificate checks turned off"),
    (None, False, "", "download", BlenderSourcesError, "certificate checks turned off"),
], ids=["http", "user-info", "empty-list", "unreadable-file", "verify-off-download", "verify-off-listing",
        "verify-none", "verify-zero", "verify-empty-path"])
def test_a_bad_setting_or_session_raises_and_sends_nothing(
        monkeypatch, settings, tmp_path, env_value, unreadable, verify, call, error, message):
    """BS-CFG-2, BS-SRC-3, BS-DL-7, AC-3: no fallback to the defaults and no request; the error names the
    setting and the entry's position. BS-DL-7: any ``verify`` other than ``True`` or a CA bundle path is
    refused (``None``, ``0`` and ``""`` turn checks off in ``requests`` as ``False`` does)."""
    if env_value is not None:
        monkeypatch.setenv(SOURCES_ENV, env_value)
    if unreadable:
        monkeypatch.setattr(settings_file, "read_settings", _unreadable)
    session = FakeSession(verify=verify)

    with pytest.raises(error, match=message):
        if call == "download":
            download_build(entry(), tmp_path, session=session)
        else:
            list_series(session=session)

    assert session.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("verify", [True, "/etc/ssl/certs/ca-bundle.pem"], ids=["true", "ca-bundle-path"])
def test_a_checking_session_is_accepted(verify):
    """BS-DL-7: ``verify=True`` and a CA bundle path keep certificate checks on; the session is used."""
    assert sources.check_session(FakeSession(verify=verify)) is None


START = "https://a.test/release/Blender4.5/f"


def _hops(count):
    """``count`` redirects ``hop0 -> hop1 -> ... -> hop<count>``: (routes, every URL of the chain)."""
    urls = [f"https://a.test/hop{i}" for i in range(count + 1)]
    return {urls[i]: redirect(302, urls[i + 1]) for i in range(count)}, urls


def _too_many():
    routes, urls = _hops(MAX_REDIRECTS + 1)
    return routes, urls[:-1], "too many redirects"  # the 11th 3xx is not followed


# Each case: (routes, the URLs requested in order, failure reason or None). With None the last URL
# answers 200 and that response is returned.
_CHAINS = {
    "absolute-302": lambda: ({START: redirect(302, "https://b.test/mirror/f")},
                             [START, "https://b.test/mirror/f"], None),
    "relative-301-then-308": lambda: ({START: redirect(301, "/mirror/f"), "https://a.test/mirror/f": redirect(308, "g")},
                                      [START, "https://a.test/mirror/f", "https://a.test/mirror/g"], None),
    "ten-redirects": lambda: (*_hops(MAX_REDIRECTS), None),
    "eleven-redirects": _too_many,
    "302-without-location": lambda: ({START: redirect(302)}, [START], "HTTP 302"),
    "unparsable-location": lambda: ({START: redirect(301, "https://[::1/f")}, [START], "bad redirect"),
}


@pytest.mark.parametrize("chain", list(_CHAINS))
def test_get_checked_follows_redirects_without_reading_them(chain):
    """BS-SRC-4 (security review: ``requests`` reads the whole body of every 3xx it follows): redirects
    are followed one request at a time, absolute or relative, at most ``MAX_REDIRECTS``; no 3xx body is
    read (reading one fails the test), every response not returned is closed, and a 3xx with no usable
    ``Location`` fails the source. Each request streams and asks for ``Accept-Encoding: identity``."""
    routes, requested, reason = _CHAINS[chain]()
    final = FakeResponse(body=b"ok")
    if reason is None:
        routes[requested[-1]] = final
    session = FakeSession(routes)

    if reason is None:
        assert sources.get_checked(session, requested[0], (5, 15)) is final
        assert not final.closed
    else:
        with pytest.raises(SourceFailure) as info:
            sources.get_checked(session, requested[0], (5, 15))
        assert info.value.reason == reason

    assert session.urls == requested
    assert all(response.closed for response in routes.values() if response is not final)
    assert all(streamed_identity(call) for call in session.calls)


@pytest.mark.parametrize("value, expected", [
    ("4.5.8", True),
    ("4.5.8\n", False),
    ("4.5.٨", False),
], ids=["x-y-z", "trailing-newline", "non-ascii-digit"])
def test_is_version_takes_ascii_x_y_z_only(value, expected):
    """BS-REL-7: a version is exactly ``X.Y.Z`` in ASCII digits; a trailing newline or another script's
    digit is not one (it would reach file and folder names)."""
    assert is_version(value) is expected


@pytest.mark.parametrize("system, machine, expected", [
    ("Windows", "AMD64", "windows-x64"),
    ("Windows", "ARM64", "windows-arm64"),
    ("Linux", "x86_64", "linux-x64"),
    ("Linux", "aarch64", "linux-arm64"),
    ("Darwin", "x86_64", "macos-x64"),
    ("Darwin", "arm64", "macos-arm64"),
    ("Linux", "i686", None),
    ("FreeBSD", "amd64", None),
])
def test_platform_ids(system, machine, expected):
    """BS-SRC-6: one table for every platform id; Windows ARM64 is ``windows-arm64``, not ``windows-x64``."""
    assert current_platform_id(system, machine) == expected
