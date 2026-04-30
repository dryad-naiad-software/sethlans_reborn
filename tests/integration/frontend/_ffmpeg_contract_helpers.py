# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the wizard-ffmpeg-rewrite frontend-integration
contract tests.

* TS interface parsing (regex-based field extraction).
* The ``_seed`` helper that publishes a deterministic FFmpeg status
  into the in-process registry, mirroring the production check
  thread's ``_publish`` path.
"""

from __future__ import annotations

import re
from pathlib import Path

from workers.services.parts_check import registry

URL = "/api/ffmpeg-status/"

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = REPO_ROOT / "manager" / "frontend" / "src" / "app"
FFMPEG_SERVICE_TS = (
    FRONTEND_ROOT / "core" / "services" / "ffmpeg-status.service.ts"
)
SYSTEM_STATUS_MODELS_TS = (
    FRONTEND_ROOT / "features" / "admin" / "system-status"
    / "system-status.models.ts"
)
JOB_CREATE_FORM_TS = (
    FRONTEND_ROOT / "features" / "projects"
    / "job-create-form.component.ts"
)

EXPECTED_REGULAR_KEYS = {"video_assembly_ready"}
EXPECTED_DETAILS_KEYS = {
    "source", "version", "path", "status", "error",
}


# Match `<field>?: <type>;` or `<field>: <type>;` inside a TS interface
# body. Stops at `;` so multi-line union types collapse onto a single
# field.
_TS_FIELD_RE = re.compile(
    r"^\s*(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\??\s*:\s*(?P<type>[^;]+);",
    re.MULTILINE,
)


def parse_ts_interface(source: str, name: str) -> dict[str, str]:
    """Return ``{field_name: ts_type}`` for the named TS interface.

    Raises ``AssertionError`` if the interface is not found — keeps the
    contract tests self-validating against future TS renames.
    """
    pattern = re.compile(
        r"export\s+interface\s+" + re.escape(name) + r"\s*\{([^}]*)\}",
        re.DOTALL,
    )
    match = pattern.search(source)
    assert match, f"Could not find TS interface {name!r} in source"
    body = match.group(1)
    return {
        m.group("name"): m.group("type").strip()
        for m in _TS_FIELD_RE.finditer(body)
    }


def is_optional_field(source: str, interface: str, field: str) -> bool:
    """Return True if ``<field>?:`` (with the question mark) appears."""
    pattern = re.compile(
        r"export\s+interface\s+" + re.escape(interface)
        + r"\s*\{([^}]*)\}",
        re.DOTALL,
    )
    match = pattern.search(source)
    assert match
    body = match.group(1)
    return bool(re.search(rf"\b{re.escape(field)}\?\s*:", body))


def seed_ffmpeg_status(
    status="installing", source="", version="", path="", error=None,
):
    """Publish a deterministic FFmpeg status into the registry.

    Drives the API response through every state without invoking the
    real ``check_ffmpeg`` — uses the same ``_publish`` helper the
    production check thread uses.
    """
    registry._publish(
        "ffmpeg",
        registry.Status(
            status=status, source=source, version=version,
            path=path, error=error,
        ),
    )
