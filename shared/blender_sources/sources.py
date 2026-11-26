# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ordered list of Blender sources, its setting, the last-worked source and the checked GET.

**List (BS-SRC-2, BS-CFG-1).** The default is the official site, then three
mirrors, all HTTPS. ``SETHLANS_BLENDER_SOURCES`` beats ``sethlans.ini``
``[blender] sources``, which beats the default; both hold comma-separated
URLs, and a set value replaces the whole default list. The setting is read
again at every lookup and download (BS-CFG-2). An unreadable file or any
invalid entry raises ``BlenderSourcesConfigError``; there is no fallback.

**Last worked (BS-DL-1, BS-DL-6).** The source that last completed a
verified download in this process, held in memory under ``_last_lock``
(a leaf lock) and never written to disk. Only downloads use it; the release
list is always asked in list order.

**Checked GET (BS-SRC-4, BS-DL-7).** Every request goes through
:func:`get_checked`: only a session whose ``verify`` is ``True`` or a CA
bundle path is used, every URL of a redirect chain must be HTTPS, and any
final status other than 200 fails the source. Requests always stream and ask
for ``Accept-Encoding: identity``; a compressed answer fails the source before
a byte of its body is read, so the callers' byte caps count the bytes that
reach memory or disk (a small gzip body can unpack to gigabytes).
``get_checked`` follows redirects itself, one request at a time
(:func:`send_without_redirects`, which the worker's manager step uses too),
and closes each 3xx unread: requests 2.32.4 reads and decodes the whole body
of every 3xx it sees, even with ``stream=True`` and ``allow_redirects=False``
(``Session.send`` builds ``Response.next`` through ``resolve_redirects``,
which reads ``resp.content``), so ``Session.get`` is not used at all.
"""
import os
import threading
from typing import List, Optional
from urllib.parse import urljoin, urlsplit

import requests

from shared.blender_sources.errors import (
    BlenderSourcesConfigError, BlenderSourcesError, SourceFailure, request_failure_reason,
)

DEFAULT_SOURCES = (
    "https://download.blender.org/release/",
    "https://mirror.clarkson.edu/blender/release/",
    "https://ftp.halifax.rwth-aachen.de/blender/release/",
    "https://ftp.nluug.nl/pub/graphics/blender/release/",
)
SOURCES_ENV = "SETHLANS_BLENDER_SOURCES"
SETTINGS_SECTION = "blender"
SETTINGS_KEY = "sources"
IDENTITY_HEADERS = {"Accept-Encoding": "identity"}  # no compressed bodies (see get_checked)
REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
MAX_REDIRECTS = 10

_last_lock = threading.Lock()  # leaf lock; guards _last_worked
_last_worked: Optional[str] = None


def _check_entry(raw: str, origin: str, position: int) -> str:
    """One validated, normalised source URL (BS-SRC-3)."""
    where = f"{origin} entry {position}"
    entry = raw.strip()
    if not entry:
        raise BlenderSourcesConfigError(f"{where} is empty")
    if any(ch.isspace() or ord(ch) < 32 for ch in entry):
        raise BlenderSourcesConfigError(f"{where} contains whitespace or control characters")
    try:
        parts = urlsplit(entry)
        hostname = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        raise BlenderSourcesConfigError(f"{where} is not a valid URL") from None
    if parts.scheme != "https":
        raise BlenderSourcesConfigError(f"{where} must use https")
    if not hostname:
        raise BlenderSourcesConfigError(f"{where} has no host")
    if "@" in parts.netloc:
        raise BlenderSourcesConfigError(f"{where} must not contain user info")
    if parts.query or parts.fragment or "?" in entry or "#" in entry:
        raise BlenderSourcesConfigError(f"{where} must not contain a query or fragment")
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return f"https://{parts.netloc.lower()}{path}"


def parse_source_list(value: str, origin: str) -> List[str]:
    """Validate a comma-separated list; a missing ``/`` is added, a repeat dropped (first kept)."""
    if not value.strip():
        raise BlenderSourcesConfigError(f"{origin} is set but lists no source")
    result: List[str] = []
    for position, raw in enumerate(value.split(","), start=1):
        entry = _check_entry(raw, origin, position)
        if entry not in result:
            result.append(entry)
    return result


def _from_settings_file() -> Optional[List[str]]:
    from shared import settings_file
    try:
        parser = settings_file.read_settings()
    except OSError as exc:  # SettingsFileError is an OSError
        raise BlenderSourcesConfigError(f"cannot read the Blender sources from the settings file: {exc}") from None
    if not parser.has_option(SETTINGS_SECTION, SETTINGS_KEY):
        return None
    origin = f"{settings_file.settings_path()} [{SETTINGS_SECTION}] {SETTINGS_KEY}"
    return parse_source_list(parser.get(SETTINGS_SECTION, SETTINGS_KEY), origin)


def configured_sources() -> List[str]:
    """The source list now in force: env, else ``sethlans.ini``, else the default (BS-CFG-1, BS-CFG-2)."""
    env_value = os.environ.get(SOURCES_ENV)
    if env_value is not None:
        return parse_source_list(env_value, SOURCES_ENV)
    from_file = _from_settings_file()
    return from_file if from_file is not None else list(DEFAULT_SOURCES)


def last_worked() -> Optional[str]:
    """The source of the last verified download in this process, or None."""
    with _last_lock:
        return _last_worked


def set_last_worked(source: Optional[str]) -> None:
    """Record ``source`` after a checksum match (BS-DL-6); None forgets it (tests)."""
    global _last_worked
    with _last_lock:
        _last_worked = source


def download_order(sources: List[str]) -> List[str]:
    """Last-worked first when still listed, then the rest in list order; a fresh list each call (BS-DL-2)."""
    last = last_worked()
    if last is None or last not in sources:
        return list(sources)
    return [last] + [s for s in sources if s != last]


def check_session(session) -> None:
    """Refuse a session whose ``verify`` is not ``True`` or a CA bundle path (BS-DL-7)."""
    if session is None:
        return
    verify = getattr(session, "verify", None)
    if verify is not True and not (isinstance(verify, str) and verify):  # requests treats "" as off
        raise BlenderSourcesError(
            "refusing a requests session with certificate checks turned off (verify must be True or a CA bundle path)")


def check_identity_encoding(response) -> None:
    """Fail the source when the body is compressed (its size would not be capped in memory)."""
    encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise SourceFailure("compressed response")


def send_without_redirects(session, url: str, timeout, headers=None):
    """One streamed GET of ``url`` through the session's transport adapter; no redirect is followed.

    The same steps as ``Session.request`` (session headers, environment proxies
    and CA bundle, mounted adapter) without ``Session.send``, whose redirect
    handling reads a 3xx body in full (see the module docstring). ``headers``
    are sent on top of the session's; ``Accept-Encoding: identity`` always
    wins. A 3xx is returned unread; requests exceptions propagate. The body is
    not read yet; the response is the caller's to close.
    """
    request = requests.Request("GET", url, headers={**(headers or {}), **IDENTITY_HEADERS})
    prepared = session.prepare_request(request)
    settings = session.merge_environment_settings(prepared.url, {}, True, None, None)  # stream=True
    return session.get_adapter(prepared.url).send(prepared, timeout=timeout, **settings)


def _redirect_target(current: str, location: str) -> str:
    """The HTTPS URL ``location`` points to from ``current`` (BS-SRC-4)."""
    try:
        target = urljoin(current, location)
        scheme = urlsplit(target).scheme
    except ValueError:  # e.g. a bad IPv6 host
        raise SourceFailure("bad redirect") from None
    if scheme != "https":
        raise SourceFailure("redirected to non-HTTPS")
    return target


def get_checked(session, url: str, timeout):
    """GET ``url`` (streamed, uncompressed); return it only for HTTP 200 over HTTPS all the way.

    Redirects (301, 302, 303, 307, 308 with ``Location``) are followed here,
    at most ``MAX_REDIRECTS`` of them; every URL is checked before anything is
    sent to it, and each 3xx is closed without reading its body. Raises
    ``SourceFailure`` with the BS-LOG-1 reason for every source-side failure.
    The returned body is not read yet; the response is the caller's to close.
    """
    if urlsplit(url).scheme != "https":
        raise SourceFailure("redirected to non-HTTPS")
    current, followed = url, 0
    while True:
        try:
            response = send_without_redirects(session, current, timeout)
        except requests.exceptions.RequestException as exc:
            raise SourceFailure(request_failure_reason(exc, timeout)) from None
        try:
            location = response.headers.get("Location") if response.status_code in REDIRECT_STATUSES else None
            if not location:
                if response.status_code != 200:
                    raise SourceFailure(f"HTTP {response.status_code}")
                check_identity_encoding(response)
                return response
            if followed == MAX_REDIRECTS:
                raise SourceFailure("too many redirects")
            current = _redirect_target(current, location)
        except BaseException:
            response.close()
            raise
        response.close()  # a 3xx followed: its body is never read
        followed += 1
