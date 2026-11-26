# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The three release-list lookups (BS-REL-1 .. BS-REL-3, BS-REL-8, D11).

Each lookup asks the sources in list order (never last-worked order) and
takes the first that answers it fully:

- :func:`list_series`: the index page; full = HTTP 200 and at least one
  ``Blender<X>.<Y>/`` link with ``X >= 4``.
- :func:`latest_patch`: the series page; full = HTTP 200 and at least one
  build link of that series.
- :func:`find_build`: the series page plus ``blender-<version>.sha256``; full
  = both HTTP 200, the platform's build listed and a checksum line for it.
  The cache is read first; only a full answer is written to it (#321).

Each body is read in chunks and may hold at most ``MAX_LISTING_BYTES``;
more fails the source with "too large". A source that fails any request of
a lookup, or lacks the wanted item, has not answered; the next one is
asked. Each failure is one WARNING naming the lookup, version and platform,
the host and the reason (BS-LOG-1). All failed:
``AllSourcesFailedError``; for ``find_build``, when a series page answered
and no source lists the build, ``BuildNotPublishedError``.
"""
import logging
from typing import Callable, Dict, List, TypeVar

import requests

from shared.blender_sources import release_cache, sources
from shared.blender_sources.errors import (
    AllSourcesFailedError, BuildNotPublishedError, SourceFailure, host_of, request_failure_reason,
)
from shared.blender_sources.listing import parse_build_links, parse_checksums, parse_series_index
from shared.blender_sources.platforms import (
    SERIES_RE, is_platform_id, is_version, series_of, version_key,
)
from shared.blender_sources.release_cache import BuildEntry

logger = logging.getLogger(__name__)

LISTING_TIMEOUT = (5, 15)  # (connect, read) seconds per listing request (#113)
MAX_LISTING_BYTES = 4 * 1024 * 1024  # a listing page or checksum file larger than this fails the source
CHUNK_BYTES = 64 * 1024

T = TypeVar("T")


def _ask_each(subject: str, ask: Callable[..., T], session, timeout) -> T:
    """``ask(source)`` for each source in list order until one answers; collect failures."""
    sources.check_session(session)
    source_list = sources.configured_sources()
    own = session is None
    session = requests.Session() if own else session
    failures = []
    try:
        for source in source_list:
            try:
                return ask(source, session, timeout)
            except SourceFailure as exc:
                logger.warning("%s: %s failed: %s", subject, host_of(source), exc.reason)
                failures.append((source, exc.reason))
    finally:
        if own:
            session.close()
    raise AllSourcesFailedError(subject, failures)


def _read_body(response, timeout) -> bytes:
    """At most ``MAX_LISTING_BYTES`` of the body; more fails the source with "too large"."""
    declared = response.headers.get("Content-Length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_LISTING_BYTES:
        raise SourceFailure("too large")
    body = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
            body += chunk
            if len(body) > MAX_LISTING_BYTES:
                raise SourceFailure("too large")
    except requests.exceptions.RequestException as exc:
        raise SourceFailure(request_failure_reason(exc, timeout)) from None
    return bytes(body)


def _decode(response, body: bytes) -> str:
    """``body`` decoded with the declared charset, else UTF-8; bad bytes become U+FFFD."""
    try:
        return body.decode(response.encoding or "utf-8", errors="replace")
    except LookupError:  # an unknown charset name; the names used are plain ASCII
        return body.decode("utf-8", errors="replace")


def _text(session, url: str, timeout, what: str = "") -> str:
    """The body of ``url``; a failure's reason names ``what`` was asked for (BS-LOG-1)."""
    try:
        response = sources.get_checked(session, url, timeout)
        try:
            return _decode(response, _read_body(response, timeout))
        finally:
            response.close()
    except SourceFailure as exc:
        raise SourceFailure(f"{what}: {exc.reason}" if what else exc.reason) from None


def list_series(*, session=None, timeout=LISTING_TIMEOUT) -> List[str]:
    """Every Blender series ``>= 4.0`` on the first source that lists one, oldest first."""
    def ask(source, sess, tmo):
        found = parse_series_index(_text(sess, source, tmo))
        if not found:
            raise SourceFailure("not listed")
        return found
    return _ask_each("Blender release list (index)", ask, session, timeout)


def latest_patch(series: str, *, session=None, timeout=LISTING_TIMEOUT) -> str:
    """The highest ``X.Y.Z`` of ``series`` (``"4.5"``) on the first source that lists one."""
    if not isinstance(series, str) or not SERIES_RE.fullmatch(series):
        raise ValueError(f"not a Blender series: {series!r}")

    def ask(source, sess, tmo):
        builds = parse_build_links(_text(sess, f"{source}Blender{series}/", tmo))
        versions = [v for v in builds if series_of(v) == series]
        if not versions:
            raise SourceFailure("not listed")
        return max(versions, key=version_key)
    return _ask_each(f"Blender release list (series page) {series}", ask, session, timeout)


def _full_answer(source, version, builds: Dict[str, str], sums: Dict[str, str]) -> Dict[str, BuildEntry]:
    """Entries for every platform of ``version`` that has both a build link and a checksum line."""
    return {
        platform_id: BuildEntry(version, platform_id, name, sums[name], source)
        for platform_id, name in builds.items() if name in sums
    }


def find_build(version: str, platform_id: str, cache_file=None, *,
               session=None, timeout=LISTING_TIMEOUT) -> BuildEntry:
    """The build of ``version`` for ``platform_id``, with its official SHA-256 (BS-REL-1, D11).

    ``cache_file`` is read first and gets every full answer merged into it;
    None means no cache.
    """
    if not is_version(version) or not is_platform_id(platform_id):
        raise ValueError(f"not a Blender version and platform: {version!r} {platform_id!r}")
    cached = release_cache.lookup(cache_file, version, platform_id)
    if cached is not None:
        return cached
    series = series_of(version)
    seen = {"page": False, "listed": False}

    def ask(source, sess, tmo):
        builds = parse_build_links(_text(sess, f"{source}Blender{series}/", tmo, "series page"))
        seen["page"] = seen["page"] or bool(builds)
        wanted = builds.get(version, {})
        if platform_id not in wanted:
            raise SourceFailure("not listed")
        seen["listed"] = True
        sums = parse_checksums(_text(sess, f"{source}Blender{series}/blender-{version}.sha256", tmo, "checksum file"))
        if wanted[platform_id] not in sums:
            raise SourceFailure("checksum not listed")
        return _full_answer(source, version, wanted, sums)

    subject = f"Blender {version} {platform_id}"
    try:
        entries = _ask_each(subject, ask, session, timeout)
    except AllSourcesFailedError as exc:
        if seen["page"] and not seen["listed"]:
            raise BuildNotPublishedError(version, platform_id, exc.failures) from None
        raise
    release_cache.merge(cache_file, version, entries)
    return entries[platform_id]
