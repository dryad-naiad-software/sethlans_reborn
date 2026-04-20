# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cross-platform Caddy binary fetcher for Sethlans Reborn.

Thin CLI wrapper; the implementation lives in :mod:`tools.caddy_fetch`
(split across ``lockfile.py``, ``download.py``, ``gpg.py``, and
``cli.py`` to keep each file under the 300-line Python limit).

Usage:
    # Single-platform (auto-detect host)
    python tools/fetch_caddy.py --target-dir .venv-build/caddy

    # Explicit platform
    python tools/fetch_caddy.py --target-dir .venv-build/caddy \\
        --platform linux-amd64

    # Multi-arch (Docker build context)
    python tools/fetch_caddy.py --target-dir .tmp/caddy \\
        --multi-arch linux-amd64,linux-arm64

Exit codes:
    0 = success
    1 = network / IO / argument error
    2 = SHA-256 integrity failure
    3 = GPG signature verification failure

Supported platforms (keys in ``tools/caddy.lock``):
    linux-amd64, linux-arm64, darwin-amd64, darwin-arm64, windows-amd64
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow ``python tools/fetch_caddy.py`` (direct invocation) to resolve
# the ``caddy_fetch`` sibling package regardless of CWD or PYTHONPATH.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from caddy_fetch.cli import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
