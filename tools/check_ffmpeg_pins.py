# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Check that every pinned FFmpeg download still matches its pinned SHA-256.

The manager downloads FFmpeg from ``FFMPEG_URLS`` and verifies it against
``FFMPEG_SHA256`` (both in
``manager/workers/services/parts_check/ffmpeg_download_pkg/constants.py``).
The unit tests mock the download, so a pin that has gone stale upstream is
only caught by actually downloading it.  This script does that.

The constants module is loaded by file path, so neither Django nor the
``workers`` package is imported.  Only the standard library is used.

Usage:
    python tools/check_ffmpeg_pins.py
    python tools/check_ffmpeg_pins.py --only linux-x64

Exit codes:
    0 = every checked URL matched its pinned digest
    1 = any mismatch, download error, or invalid pin table
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONSTANTS_PATH = (
    REPO_ROOT / "manager" / "workers" / "services" / "parts_check"
    / "ffmpeg_download_pkg" / "constants.py"
)

PLACEHOLDER = "PLACEHOLDER_FILL_AT_RELEASE"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

MAX_BYTES = 512 * 1024 * 1024      # refuse anything larger than 512 MiB
# Time budget: the deadline is checked between chunks, so one URL can take
# up to DOWNLOAD_DEADLINE_S + SOCKET_TIMEOUT_S (270 s).  Four distinct URLs
# is 18 min worst case, inside the workflow's 20-minute job timeout, so a
# slow run still ends with ERROR lines and a summary instead of a cancel.
SOCKET_TIMEOUT_S = 30              # per blocking socket operation
DOWNLOAD_DEADLINE_S = 240          # whole-download wall clock per URL
CHUNK_BYTES = 1024 * 1024
USER_AGENT = "ffmpeg-pin-check/1.0"


class CheckError(Exception):
    """A download could not be checked (reason goes in the ERROR line)."""


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only when the new location is also HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        scheme = urllib.parse.urlsplit(newurl).scheme.lower()
        if scheme != "https":
            if fp is not None:
                fp.close()
            raise CheckError(f"refused redirect to non-HTTPS URL: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def load_pins(path: Path = CONSTANTS_PATH) -> tuple[dict, dict]:
    """Load ``FFMPEG_URLS`` and ``FFMPEG_SHA256`` from the constants file."""
    spec = importlib.util.spec_from_file_location("_ffmpeg_pin_constants", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load constants from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    urls = getattr(module, "FFMPEG_URLS", None)
    digests = getattr(module, "FFMPEG_SHA256", None)
    if not isinstance(urls, dict) or not isinstance(digests, dict):
        raise RuntimeError(f"FFMPEG_URLS / FFMPEG_SHA256 dicts missing in {path}")
    return urls, digests


def validate_tables(urls: dict, digests: dict) -> list[str]:
    """Return a list of problems with the pin tables themselves."""
    problems = []
    for pid in sorted(set(urls) - set(digests)):
        problems.append(f"{pid}: in FFMPEG_URLS but not in FFMPEG_SHA256")
    for pid in sorted(set(digests) - set(urls)):
        problems.append(f"{pid}: in FFMPEG_SHA256 but not in FFMPEG_URLS")
    for pid in sorted(set(urls) & set(digests)):
        digest = digests[pid]
        if not digest or digest == PLACEHOLDER:
            problems.append(f"{pid}: digest is empty or the placeholder")
        elif not isinstance(digest, str) or not SHA256_RE.match(digest):
            problems.append(f"{pid}: digest is not 64 hex chars: {digest!r}")
        url = urls[pid]
        if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme.lower() != "https":
            problems.append(f"{pid}: URL is not HTTPS: {url!r}")
    return problems


def group_by_url(urls: dict, digests: dict, only: str | None) -> dict:
    """Map each distinct URL to the platform ids that pin it."""
    groups: dict[str, list[str]] = {}
    for pid in sorted(set(urls) & set(digests)):
        if only is not None and pid != only:
            continue
        digest = digests[pid]
        if not isinstance(digest, str) or not SHA256_RE.match(digest):
            continue  # already reported by validate_tables()
        if not isinstance(urls[pid], str):
            continue  # already reported by validate_tables()
        groups.setdefault(urls[pid], []).append(pid)
    return groups


def _open(url: str):
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = opener.open(request, timeout=SOCKET_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise CheckError(f"HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise CheckError(f"connection failed: {exc.reason}") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise CheckError(f"connection failed: {exc}") from exc
    final = response.geturl()
    if urllib.parse.urlsplit(final).scheme.lower() != "https":
        response.close()
        raise CheckError(f"final URL is not HTTPS: {final}")
    return response


def sha256_of_url(url: str) -> tuple[str, int]:
    """Stream ``url`` into SHA-256; return (hex digest, byte count)."""
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise CheckError("refused non-HTTPS URL")
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_S
    hasher = hashlib.sha256()
    total = 0
    with _open(url) as response:
        length = response.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > MAX_BYTES:
            raise CheckError(f"Content-Length {length} exceeds cap of {MAX_BYTES} bytes")
        while True:
            try:
                chunk = response.read(CHUNK_BYTES)
            except (OSError, http.client.HTTPException) as exc:
                raise CheckError(f"read failed after {total} bytes: {exc}") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise CheckError(f"body exceeds cap of {MAX_BYTES} bytes")
            if time.monotonic() > deadline:
                raise CheckError(f"download exceeded {DOWNLOAD_DEADLINE_S} s")
            hasher.update(chunk)
        if length and length.isdigit() and total != int(length):
            raise CheckError(f"short read: got {total} of {length} bytes")
    return hasher.hexdigest(), total


def check_url(url: str, pids: list[str], digests: dict) -> bool:
    """Download one URL and print its OK / MISMATCH / ERROR line."""
    label = ", ".join(pids)
    # The manager compares lowercase hex on both sides; do the same.
    expected = {digests[pid].lower() for pid in pids}
    if len(expected) != 1:
        print(f"ERROR    [{label}] {url}: platform ids sharing this URL pin "
              f"different digests: {sorted(expected)}")
        return False
    want = expected.pop()
    try:
        actual, size = sha256_of_url(url)
    except CheckError as exc:
        print(f"ERROR    [{label}] {url}: {exc}")
        return False
    if actual == want:
        print(f"OK       [{label}] {url} ({size} bytes, sha256={actual})")
        return True
    print(f"MISMATCH [{label}] {url} ({size} bytes)\n"
          f"         expected={want}\n"
          f"         actual=  {actual}")
    return False


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download every pinned FFmpeg URL and check its SHA-256.",
    )
    parser.add_argument(
        "--only", metavar="PLATFORM_ID",
        help="check only this platform id (e.g. linux-x64)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        urls, digests = load_pins()
    except Exception as exc:  # noqa: BLE001 - any load failure is fatal
        print(f"ERROR    cannot load pins: {exc}")
        return 1

    ok = True
    for problem in validate_tables(urls, digests):
        print(f"ERROR    {problem}")
        ok = False

    if args.only is not None and args.only not in urls:
        print(f"ERROR    unknown platform id {args.only!r}; "
              f"known: {', '.join(sorted(urls))}")
        return 1

    groups = group_by_url(urls, digests, args.only)
    if not groups:
        print("ERROR    no URLs to check")
        return 1
    for url, pids in groups.items():
        ok = check_url(url, pids, digests) and ok

    print("All FFmpeg pins verified." if ok else "FFmpeg pin check FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
