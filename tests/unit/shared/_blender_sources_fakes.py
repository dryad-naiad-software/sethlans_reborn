# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""A fake ``requests`` transport and the shared fixtures of the ``shared.blender_sources`` tests.

No test here touches the network. :class:`FakeSession` is a real
``requests.Session`` (real request preparation and header merging, no
environment settings) whose ``http://`` and ``https://`` adapters answer from
a ``{url: answer}`` table and record each request. An answer is a
:class:`FakeResponse`, or an exception instance to raise from the adapter.
A URL not in the table answers 404.
"""
import hashlib

import pytest
import requests

from shared.blender_sources import SOURCES_ENV, BuildEntry, sources

A = "https://a.test/release/"
B = "https://b.test/release/"
C = "https://c.test/release/"

VERSION = "4.5.8"
PLATFORM = "linux-x64"
FILE = "blender-4.5.8-linux-x64.tar.xz"
BODY = b"blender archive bytes"
SHA = hashlib.sha256(BODY).hexdigest()


class FakeResponse:
    """The parts of ``requests.Response`` the package reads.

    Bodies are read only through ``iter_content`` (no ``text``). ``chunks``
    is a list of byte strings or an iterator; an exception instance in the
    list is raised when reached.
    """

    encoding = None  # no declared charset: the listing reader decodes UTF-8

    def __init__(self, status=200, body=b"", headers=None, chunks=None):
        self.status_code = status
        self.content = body
        self.headers = dict(headers or {})
        self._chunks = chunks if chunks is not None else [body]
        self.closed = False

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class UnreadableResponse(FakeResponse):
    """A response whose body must never be read: ``content`` or ``iter_content`` fails the test."""

    @property
    def content(self):
        raise AssertionError(f"the body of an HTTP {self.status_code} was read")

    @content.setter
    def content(self, _value):
        pass

    def iter_content(self, chunk_size=1):
        raise AssertionError(f"the body of an HTTP {self.status_code} was read")


def redirect(status, location=None):
    """A 3xx to ``location`` (no ``Location`` header when None) whose body must stay unread."""
    return UnreadableResponse(status=status, headers={"Location": location} if location is not None else None)


class _FakeAdapter(requests.adapters.BaseAdapter):
    def __init__(self, session):
        super().__init__()
        self._session = session

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        return self._session.answer(request, timeout, stream)

    def close(self):
        pass


class FakeSession(requests.Session):
    """Answers from ``routes``; records ``(url, timeout, stream, headers)`` per request sent."""

    def __init__(self, routes=None, verify=True):
        super().__init__()
        self.trust_env = False  # no proxy, CA bundle or netrc from the machine running the tests
        self.routes = dict(routes or {})
        self.verify = verify
        self.calls = []
        adapter = _FakeAdapter(self)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def answer(self, request, timeout, stream):
        self.calls.append((request.url, timeout, stream, dict(request.headers)))
        answer = self.routes.get(request.url, FakeResponse(status=404))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def urls(self):
        return [call[0] for call in self.calls]


def streamed_identity(call):
    """True when a recorded request was streamed and asked for an uncompressed body (BS-DL-9, #323)."""
    return call[2] is True and call[3].get("Accept-Encoding") == "identity"


def build_url(source, file_name=FILE, series="4.5"):
    return f"{source}Blender{series}/{file_name}"


def entry(sha256=SHA, source=A):
    return BuildEntry(VERSION, PLATFORM, FILE, sha256, source)


@pytest.fixture
def three_sources(monkeypatch):
    """Sources A, B, C (env setting) and no last-worked source, before and after the test."""
    monkeypatch.setenv(SOURCES_ENV, ",".join((A, B, C)))
    sources.set_last_worked(None)
    yield (A, B, C)
    sources.set_last_worked(None)
