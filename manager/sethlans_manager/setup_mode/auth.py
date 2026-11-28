# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The setup token, the setup session and the failed-attempt limit (FR-SEC-1..3).

* **Token.** The launcher writes ``<settings>/setup/setup_token``
  (owner-only) before it starts setup mode; :func:`load_token` reads it
  once at start. It is never logged.
* **Session.** ``POST /api/setup/auth/`` with the right token issues one
  session token (a new one replaces the old). It travels as the
  ``sethlans_setup_session`` cookie (``HttpOnly; Secure; SameSite=Strict;
  Path=/``) and, on every API call, in the ``X-Setup-Session`` header; both
  must match. It expires after 2 hours without an authenticated request.
* **Limit.** 10 wrong tokens from one client address within 60 s: the
  next attempts answer 429 until the oldest failure is 60 s old. The
  address is ``REMOTE_ADDR``, which the setup server sets to the real
  client (Waitress trusts Caddy's ``X-Forwarded-For``, ``serve``).
"""
import secrets
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Callable, Optional

from shared import owner_only_acl
from shared.settings_file import SETUP_SUBDIR, get_settings_dir
from shared.settings_file import SETUP_TOKEN_NAME as TOKEN_FILE_NAME
from shared.settings_file_io import read_limited

MAX_TOKEN_BYTES = 512
SESSION_COOKIE = "sethlans_setup_session"
SESSION_HEADER_META = "HTTP_X_SETUP_SESSION"
SESSION_IDLE_SECONDS = 2 * 60 * 60
MAX_FAILURES = 10
WINDOW_SECONDS = 60
MAX_TRACKED_CLIENTS = 10000

OK = "ok"
BAD = "bad"
LIMITED = "limited"


class SetupTokenError(Exception):
    """No usable setup token file."""


def token_path() -> Path:
    return get_settings_dir() / SETUP_SUBDIR / TOKEN_FILE_NAME


def load_token(path=None) -> str:
    """Read the setup token; raise :class:`SetupTokenError` (never quoting it)."""
    path = token_path() if path is None else Path(path)
    try:
        data = read_limited(path, MAX_TOKEN_BYTES)
    except FileNotFoundError:
        raise SetupTokenError(f"No setup token at {path}; start setup from the launcher") from None
    except OSError as exc:
        raise SetupTokenError(f"Cannot read the setup token at {path}: {exc.strerror}") from None
    if not owner_only_acl.is_owned_by_current_user(path):
        raise SetupTokenError(f"The setup token at {path} is not owned by the current user")
    if not owner_only_acl.verify_owner_only(path):
        raise SetupTokenError(f"The setup token at {path} can be read by other accounts; "
                              "start setup again from the launcher")
    if len(data) > MAX_TOKEN_BYTES:
        raise SetupTokenError(f"The setup token at {path} is too long")
    try:
        token = data.decode("ascii").strip()
    except UnicodeDecodeError:
        raise SetupTokenError(f"The setup token at {path} is not ASCII") from None
    if not token:
        raise SetupTokenError(f"The setup token at {path} is empty")
    return token


class RateLimiter:
    """Failed attempts per client in a sliding window, with a hard size cap.

    Clients are kept in order of their latest failure. Recording a failure
    first drops, from the front, clients whose latest failure is out of the
    window (so all of theirs are), then, when ``max_clients`` are still
    tracked, evicts the client whose latest failure is oldest. Each client
    keeps at most ``max_failures`` times. Every step is O(1) amortised.

    Nobody is blocked by another client's failures: a client is limited
    only by its own count, and eviction only forgets counts. So a flood
    from many addresses cannot lock out loopback (or anyone else); at
    worst it makes an evicted client's count start again.
    """

    def __init__(self, max_failures=MAX_FAILURES, window=WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic, max_clients=MAX_TRACKED_CLIENTS):
        self._max = max_failures
        self._window = window
        self._clock = clock
        self._max_clients = max_clients
        self._lock = threading.Lock()
        self._failures: "OrderedDict[str, deque]" = OrderedDict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._failures)

    def _drop_expired(self, now: float) -> None:
        while self._failures:
            client, times = next(iter(self._failures.items()))
            if times and now - times[-1] < self._window:
                return
            del self._failures[client]

    def blocked(self, client: str) -> bool:
        with self._lock:
            times = self._failures.get(client)
            if times is None:
                return False
            now = self._clock()
            while times and now - times[0] >= self._window:
                times.popleft()
            if not times:
                del self._failures[client]
                return False
            return len(times) >= self._max

    def record_failure(self, client: str) -> None:
        with self._lock:
            now = self._clock()
            self._drop_expired(now)
            times = self._failures.get(client)
            if times is None:
                if len(self._failures) >= self._max_clients:
                    self._failures.popitem(last=False)
                times = self._failures[client] = deque(maxlen=self._max)
            else:
                self._failures.move_to_end(client)
            times.append(now)


class Sessions:
    """One active session token at a time, with an idle expiry."""

    def __init__(self, idle_seconds=SESSION_IDLE_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._idle = idle_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._last_seen = 0.0

    def issue(self) -> str:
        with self._lock:
            self._token = secrets.token_urlsafe(32)
            self._last_seen = self._clock()
            return self._token

    def check(self, cookie: Optional[str], header: Optional[str]) -> bool:
        """Both values present and equal to the live session; refreshes it."""
        if not cookie or not header:
            return False
        with self._lock:
            if self._token is None:
                return False
            now = self._clock()
            if now - self._last_seen >= self._idle:
                self._token = None
                return False
            current = self._token.encode("ascii")
            match = secrets.compare_digest(cookie.encode("utf-8", "replace"), current)
            match = secrets.compare_digest(header.encode("utf-8", "replace"), current) and match
            if match:
                self._last_seen = now
            return match


class SetupAuth:
    """The token check behind ``POST /api/setup/auth/``."""

    def __init__(self, token: str, limiter: Optional[RateLimiter] = None, sessions: Optional[Sessions] = None):
        if not token:
            raise ValueError("setup token must not be empty")
        self._token = token.encode("ascii")
        # ``is None``, not ``or``: an empty RateLimiter has length 0 and is falsy.
        self.limiter = RateLimiter() if limiter is None else limiter
        self.sessions = Sessions() if sessions is None else sessions

    def attempt(self, provided: object, client: str) -> str:
        """:data:`OK`, :data:`BAD` (counted) or :data:`LIMITED` (not checked)."""
        if self.limiter.blocked(client):
            return LIMITED
        if isinstance(provided, str) and secrets.compare_digest(provided.encode("utf-8", "replace"), self._token):
            return OK
        self.limiter.record_failure(client)
        return BAD

    def session_ok(self, request) -> bool:
        return self.sessions.check(request.COOKIES.get(SESSION_COOKIE), request.META.get(SESSION_HEADER_META))


_auth: Optional[SetupAuth] = None


def configure(token: str, **kwargs) -> SetupAuth:
    """Install the process's :class:`SetupAuth` (``entry`` at start; tests)."""
    global _auth
    _auth = SetupAuth(token, **kwargs)
    return _auth


def current() -> SetupAuth:
    if _auth is None:
        raise RuntimeError("setup mode has no setup token configured")
    return _auth
