# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
HTTPS download + SHA-256 verification helpers.

All HTTPS requests use the system trust store via ``requests``
defaults (``verify=True``).  ``verify=False`` is forbidden — a TLS
handshake failure is a ``download_failed``, not a fallback to HTTP.

The download helper streams the response into a destination file,
then computes the SHA-256 in a second pass.  Two-pass is intentional:
even if the writer hashes incrementally we still re-read for paranoia
since the constants live in ``constants.py`` and we want bit-perfect
matching against the on-disk archive that will be extracted.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Union

import requests

logger = logging.getLogger(__name__)

# (connect, read) timeouts.  Read timeout is generous because CDNs
# legitimately pause between chunks for several seconds during large
# transfers; the budget timer / cancel-event pattern in the wizard
# version is unnecessary here because parts-check is a fire-and-forget
# daemon with no UI cancel button.
HTTP_TIMEOUT = (10, 120)

CHUNK_SIZE = 65_536


class DownloadFailedError(Exception):
    """Raised on transport, TLS, or HTTP-status failure."""


class ChecksumMismatchError(Exception):
    """Raised when the downloaded file's SHA-256 doesn't match."""


def stream_download(
    url: str, dest_file: Union[str, Path],
) -> None:
    """HTTPS GET ``url`` into ``dest_file``.

    Raises ``DownloadFailedError`` on any transport / TLS / HTTP-status
    failure; the caller logs the exception and maps to
    ``error="download_failed"``.
    """
    dest_path = Path(dest_file)
    try:
        with requests.get(
            url,
            stream=True,
            verify=True,
            timeout=HTTP_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        fh.write(chunk)
    except requests.RequestException as exc:
        raise DownloadFailedError(
            f"transport error: {exc.__class__.__name__}",
        ) from exc
    except OSError as exc:
        raise DownloadFailedError(
            f"io error during download: {exc.__class__.__name__}",
        ) from exc


def compute_sha256(path: Union[str, Path]) -> str:
    """Return the lowercase-hex SHA-256 digest of ``path``."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(
    path: Union[str, Path], expected: str,
) -> None:
    """Raise ``ChecksumMismatchError`` if ``path`` doesn't match.

    Comparison is lowercase-hex on both sides.
    """
    actual = compute_sha256(path)
    if actual.lower() != expected.lower():
        logger.error(
            "verify_sha256: %s mismatch (expected=%s actual=%s)",
            path, expected, actual,
        )
        raise ChecksumMismatchError("sha256 mismatch")
