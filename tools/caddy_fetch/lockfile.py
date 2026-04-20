# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lockfile loading, platform detection, and URL construction."""

from __future__ import annotations

import json
import platform as host_platform
from pathlib import Path

LOCKFILE_RELATIVE = Path("tools") / "caddy.lock"


def _repo_root() -> Path:
    """Return the repo root (parent of ``tools/``)."""
    # tools/caddy_fetch/lockfile.py -> tools/caddy_fetch -> tools -> <root>
    return Path(__file__).resolve().parent.parent.parent


def load_lockfile(path: Path | None = None) -> dict:
    """Read ``tools/caddy.lock`` and validate the top-level schema."""
    lock_path = path or (_repo_root() / LOCKFILE_RELATIVE)
    if not lock_path.is_file():
        raise FileNotFoundError(f"caddy.lock not found at {lock_path}")
    with lock_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    required = {"version", "url_template", "platforms"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"caddy.lock missing keys: {sorted(missing)}")

    if not data["url_template"].startswith("https://"):
        raise ValueError(
            "caddy.lock url_template must be HTTPS-only; got "
            f"{data['url_template']!r}"
        )

    if not isinstance(data["platforms"], dict) or not data["platforms"]:
        raise ValueError("caddy.lock 'platforms' must be a non-empty object")

    for plat, info in data["platforms"].items():
        for key in ("ext", "sha256"):
            if key not in info:
                raise ValueError(
                    f"caddy.lock platforms[{plat!r}] missing {key!r}"
                )
    return data


def detect_host_platform() -> str:
    """Map the current host to a ``caddy.lock`` platform key."""
    system = host_platform.system().lower()
    machine = host_platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise ValueError(f"Unsupported host architecture: {machine}")

    if system == "linux":
        return f"linux-{arch}"
    if system == "darwin":
        return f"darwin-{arch}"
    if system == "windows":
        return f"windows-{arch}"
    raise ValueError(f"Unsupported host OS: {system}")


def build_url(lock: dict, platform_key: str) -> str:
    """Render the archive URL for ``platform_key`` from the lockfile.

    Re-asserts the resulting URL is HTTPS — a malformed url_template or
    platform-level override must never downgrade the transport.
    """
    if platform_key not in lock["platforms"]:
        raise ValueError(
            f"Platform {platform_key!r} not in caddy.lock; known: "
            f"{sorted(lock['platforms'].keys())}"
        )
    info = lock["platforms"][platform_key]
    release_platform = info.get("release_platform", platform_key)
    url = lock["url_template"].format(
        version=lock["version"],
        platform=platform_key,
        release_platform=release_platform,
        ext=info["ext"],
    )
    if not url.startswith("https://"):
        raise ValueError(f"Constructed URL is not HTTPS: {url!r}")
    return url
