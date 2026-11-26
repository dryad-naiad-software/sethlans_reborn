# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager step of a Blender install: the manager first, then online (BS-WK-4..7, D5).

``fetch(version, platform_id, dest_dir, ...)`` asks
``GET <manager>/api/blender-builds/<version>/<platform>/`` over the pinned
session (``tls_adapter.get_session()``) with the worker token:

- 200: the answer must name the asked version and platform, the file name
  the worker builds itself (``blender-<version>-<platform>.<ext>``), a
  64-hex ``sha256`` and a positive ``size`` (BS-WK-6). The archive
  (``.../archive/``) is streamed into ``<dest_dir>/<file>.part`` with
  ``size`` as the exact byte limit, checked against ``sha256`` and renamed
  (BS-WK-5). Result ``SUPPLIED``.
- 202 ``queued``/``fetching``: ``WAIT`` when the caller waits for the
  manager (version sync, BS-WK-10); otherwise one WARNING and ``UNAVAILABLE``.
- Anything else (another status, no answer, a refused answer, an archive
  answer other than 200, too many bytes, a checksum mismatch): one WARNING
  and ``UNAVAILABLE``, so the caller goes online at once.

Not enrolled (no token): ``UNAVAILABLE`` with no request. The token is sent
only through a session whose adapter pins the manager's certificate. No
redirect is followed: each request goes straight to the pinned adapter
(``send_without_redirects``), and a 3xx is closed with its body unread
(requests' ``Session.send`` would read and decode it in full) and counts as
"unavailable". The online step never gets this session (BS-WK-5, BS-DL-7).
``DownloadStoppedError`` (the caller's stop event) and ``OSError`` (a local
write error) propagate; every failure removes the ``.part``.
"""
import enum
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from shared.blender_sources import build_file_name
from shared.blender_sources.download import DOWNLOAD_TIMEOUT, MAX_BYTES, write_verified_body
from shared.blender_sources.errors import SourceFailure, host_of, request_failure_reason
from shared.blender_sources.listing import is_sha256
from shared.blender_sources.sources import check_identity_encoding, send_without_redirects
from sethlans_worker_agent import config, tls_adapter
from sethlans_worker_agent.api_auth import get_auth_headers

logger = logging.getLogger(__name__)

METADATA_TIMEOUT = (5, 15)  # (connect, read) seconds; the metadata answer is small
MAX_JSON_BYTES = 64 * 1024
WAIT_STATES = ("queued", "fetching")
KNOWN_STATES = frozenset(WAIT_STATES + ("failed", "not_wanted", "not_ready"))


class ManagerAnswer(enum.Enum):
    SUPPLIED = "supplied"  # the verified archive is in place
    WAIT = "wait"  # the manager is fetching the build; ask again later (BS-WK-10)
    UNAVAILABLE = "unavailable"  # go online now


@dataclass(frozen=True)
class ManagerResult:
    answer: ManagerAnswer
    state: Optional[str] = None  # the manager's answer: "ready", "queued", "fetching", "failed", "not_wanted"
    archive: Optional[Path] = None


class _Unavailable(Exception):
    def __init__(self, reason: str, state: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.state = state


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove the partial download %s: %s", path, exc)


def _pinned_session(url: str) -> requests.Session:
    """The thread's manager session, only when it pins the manager's certificate."""
    try:
        session = tls_adapter.get_session()
    except requests.exceptions.RequestException:  # PinUnavailableError
        raise _Unavailable("no certificate fingerprint known") from None
    if not isinstance(session.get_adapter(url), tls_adapter.PinningHTTPAdapter):
        raise _Unavailable("the session does not pin the manager's certificate")
    return session


def _get(session, url: str, auth: dict, timeout, redirect_state: Optional[str] = None):
    """The streamed, unread answer to one GET with the token; a 3xx is closed unread and refused."""
    try:
        response = send_without_redirects(session, url, timeout, auth)
    except requests.exceptions.RequestException as exc:
        raise _Unavailable(request_failure_reason(exc, timeout)) from None
    code = response.status_code
    if 300 <= code < 400:
        response.close()
        raise _Unavailable(f"HTTP {code}: redirects are not followed", redirect_state)
    return response


def _json_body(response) -> object:
    """The JSON body (at most ``MAX_JSON_BYTES``), or None when it is not one."""
    try:
        check_identity_encoding(response)
        data = b""
        for chunk in response.iter_content(chunk_size=8192):
            data += chunk
            if len(data) > MAX_JSON_BYTES:
                return None
        return json.loads(data)
    except (SourceFailure, requests.exceptions.RequestException, ValueError):
        return None


def _state_of(body: object) -> Optional[str]:
    """The answer's ``status`` when it is one the manager sends (nothing else reaches a log line)."""
    state = body.get("status") if isinstance(body, dict) else None
    return state if isinstance(state, str) and state in KNOWN_STATES else None


def _checked_metadata(body: object, version: str, platform_id: str):
    """``(file_name, sha256, size)`` from a 200 answer, or ``_Unavailable`` (BS-WK-6)."""
    if not isinstance(body, dict):
        raise _Unavailable("refused answer: not a JSON object", "ready")
    file_name = build_file_name(version, platform_id)
    if body.get("version") != version or body.get("platform") != platform_id:
        raise _Unavailable("refused answer: another version or platform", "ready")
    if body.get("file_name") != file_name:
        raise _Unavailable(f"refused answer: file name is not {file_name}", "ready")
    sha256, size = body.get("sha256"), body.get("size")
    if not is_sha256(sha256):
        raise _Unavailable("refused answer: sha256 is not 64 hex characters", "ready")
    if type(size) is not int or not 0 < size <= MAX_BYTES:
        raise _Unavailable("refused answer: bad size", "ready")
    return file_name, sha256, size


def _metadata(session, url: str, auth: dict, version: str, platform_id: str, wait: bool):
    """``(state, metadata)``: ``("ready", (file_name, sha256, size))``, or ``(<wait state>, None)``
    for a wait; raises ``_Unavailable`` otherwise."""
    response = _get(session, url, auth, METADATA_TIMEOUT)
    try:
        body = _json_body(response)
        code = response.status_code
    finally:
        response.close()
    if code == 200:
        return "ready", _checked_metadata(body, version, platform_id)
    state = _state_of(body)
    if code == 202 and state in WAIT_STATES:
        if wait:
            return state, None
        raise _Unavailable(f"the manager is still fetching it ({state})", state)
    raise _Unavailable(f"HTTP {code}" + (f" ({state})" if state else ""), state)


def _download(session, url: str, auth: dict, part: Path, sha256: str, size: int, stop) -> None:
    response = _get(session, url, auth, DOWNLOAD_TIMEOUT, redirect_state="ready")
    try:
        if response.status_code != 200:
            raise _Unavailable(f"archive HTTP {response.status_code}", "ready")
        check_identity_encoding(response)
        write_verified_body(response, part, sha256, size, stop)
    except SourceFailure as exc:
        raise _Unavailable(f"archive: {exc.reason}", "ready") from None
    finally:
        response.close()


def _from_manager(base: str, auth: dict, version: str, platform_id: str, dest_dir: Path, stop,
                  wait: bool) -> ManagerResult:
    session = _pinned_session(base)
    state, metadata = _metadata(session, base, auth, version, platform_id, wait)
    if metadata is None:
        return ManagerResult(ManagerAnswer.WAIT, state=state)
    file_name, sha256, size = metadata
    dest_dir.mkdir(parents=True, exist_ok=True)
    final, part = dest_dir / file_name, dest_dir / f"{file_name}.part"
    _remove(part)  # a leftover of an earlier, interrupted install
    try:
        _download(session, f"{base}archive/", auth, part, sha256, size, stop)
        os.replace(part, final)
    except BaseException:
        _remove(part)
        raise
    return ManagerResult(ManagerAnswer.SUPPLIED, state="ready", archive=final)


def fetch(version: str, platform_id: str, dest_dir, *, stop=None, wait: bool = False) -> ManagerResult:
    """Get Blender ``version`` for ``platform_id`` from the manager into ``dest_dir`` (see the module text).

    ``version`` is a checked ``X.Y.Z`` and ``platform_id`` one of the six ids.
    ``wait``: a 202 ``queued``/``fetching`` answer returns ``WAIT`` instead
    of going online. Raises only ``DownloadStoppedError`` and ``OSError``.
    """
    subject = f"Blender {version} {platform_id}"
    auth = get_auth_headers()
    if not auth:
        logger.info("%s: not enrolled with a manager; using the online sources.", subject)
        return ManagerResult(ManagerAnswer.UNAVAILABLE)
    base = f"{config.MANAGER_API_URL}blender-builds/{version}/{platform_id}/"
    host = host_of(base)
    try:
        result = _from_manager(base, auth, version, platform_id, Path(dest_dir), stop, wait)
    except _Unavailable as exc:
        logger.warning("%s: build from the manager %s failed: %s; trying the online sources.",
                       subject, host, exc.reason)
        return ManagerResult(ManagerAnswer.UNAVAILABLE, state=exc.state)
    if result.answer is ManagerAnswer.WAIT:
        logger.info("%s: the manager %s is fetching it; asking again later.", subject, host)
    else:
        logger.info("%s: downloaded from the manager %s; checksum from the manager.", subject, host)
    return result
