# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Vendored frontend asset integrity tests.

Per Spec 1 FR-W-FE10 / AC-W-FE6 the wizard ships vendored copies of
Petite-vue and Bootstrap 5 under ``wizard/frontend/static/vendor/``,
each pinned in ``VENDORS.md`` by exact SHA-256. These tests act as the
CI guard that:

* every vendored file on disk matches its recorded hash (catches
  accidental edits, corruption, or supply-chain swaps),
* every file in the vendor directory is documented in ``VENDORS.md``
  (catches "I added a file but forgot the manifest"),
* every manifest row points at a real file (catches "I removed a file
  but forgot the manifest").

The tests never reach the network. They only hash bytes already on
disk against the strings parsed from ``VENDORS.md``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VENDOR_DIR = PROJECT_ROOT / "wizard" / "frontend" / "static" / "vendor"
MANIFEST_PATH = VENDOR_DIR / "VENDORS.md"

# A row in the manifest table looks like:
#   | filename | Vendor | Version | License | `<sha256>` | <source URL> |
# We extract (filename, sha256) pairs. The hash is wrapped in backticks
# in the published manifest; allow either backticked or bare so a
# future contributor stripping the backticks does not silently break
# the test.
_TABLE_ROW_RE = re.compile(
    r"^\|\s*([^\s|]+)\s*\|"          # file name (no spaces)
    r"\s*[^|]+\|"                    # vendor
    r"\s*[^|]+\|"                    # version
    r"\s*[^|]+\|"                    # license
    r"\s*`?([0-9a-fA-F]{64})`?\s*\|"  # sha256 (64 hex chars)
    r"\s*[^|]+\|\s*$",               # source url
)

# Header / separator rows in the markdown table that should NOT be
# parsed as vendored-file rows.
_HEADER_TOKENS = {"file", "------"}


def _sha256_of(path: Path) -> str:
    """Compute the SHA-256 of a file's bytes as a lowercase hex string."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_manifest_rows(text: str) -> dict[str, str]:
    """Return a ``{filename: sha256}`` mapping parsed from VENDORS.md.

    Skips the markdown table header row and separator row. Hash is
    normalized to lowercase for comparison stability.
    """
    rows: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Skip the header (``| File | Vendor | ... |``) and the
        # separator (``|------|------| ... |``). Both are filtered by
        # the regex anyway, but cheap pre-filter keeps intent clear.
        first_cell = stripped.split("|", 2)[1].strip().lower()
        if first_cell in _HEADER_TOKENS or set(first_cell) <= {"-", ":"}:
            continue
        match = _TABLE_ROW_RE.match(stripped)
        if not match:
            continue
        filename, sha = match.group(1), match.group(2).lower()
        rows[filename] = sha
    return rows


def _vendor_files_on_disk() -> list[Path]:
    """Return all vendored asset files in ``VENDOR_DIR`` (excluding VENDORS.md)."""
    if not VENDOR_DIR.is_dir():
        return []
    return sorted(
        p for p in VENDOR_DIR.iterdir()
        if p.is_file() and p.name != "VENDORS.md"
    )


# ---------------------------------------------------------------------------
# Top-level structure tests
# ---------------------------------------------------------------------------

def test_vendor_directory_exists():
    """The vendor directory MUST exist (Spec 1 file layout)."""
    assert VENDOR_DIR.is_dir(), f"Expected {VENDOR_DIR} to exist"


def test_vendors_manifest_exists_and_non_empty():
    """``VENDORS.md`` MUST exist and have non-trivial content."""
    assert MANIFEST_PATH.is_file(), f"Expected {MANIFEST_PATH} to exist"
    assert MANIFEST_PATH.stat().st_size > 0, "VENDORS.md is empty"


def test_vendors_manifest_has_spdx_header():
    """VENDORS.md is a project-authored doc — must carry SPDX header."""
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert "SPDX-FileCopyrightText" in text
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in text


def test_vendor_directory_has_at_least_one_file():
    """Phase B1 MUST land at least one vendored asset."""
    files = _vendor_files_on_disk()
    assert files, (
        f"No vendored files found in {VENDOR_DIR}. Phase B1 must "
        "vendor at least Petite-vue + Bootstrap 5."
    )


# ---------------------------------------------------------------------------
# Manifest <-> disk consistency
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def manifest_rows() -> dict[str, str]:
    """Parse the VENDORS.md table once for the consistency tests."""
    return _parse_manifest_rows(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_lists_at_least_one_vendored_file(manifest_rows):
    """Sanity check: the regex matched at least one row."""
    assert manifest_rows, (
        "No vendored-file rows parsed out of VENDORS.md. Either the "
        "table is missing or its format diverged from the row regex."
    )


def test_every_disk_file_is_in_manifest(manifest_rows):
    """Every file in the vendor dir MUST be documented in VENDORS.md.

    Catches "added a vendored file, forgot to update the manifest" —
    which would silently bypass the SHA-256 integrity check.
    """
    on_disk = {p.name for p in _vendor_files_on_disk()}
    documented = set(manifest_rows.keys())
    missing_from_manifest = on_disk - documented
    assert not missing_from_manifest, (
        "These vendored files exist on disk but are NOT listed in "
        f"VENDORS.md: {sorted(missing_from_manifest)}. Add a manifest "
        "row with version, source URL, SHA-256, and license."
    )


def test_every_manifest_entry_exists_on_disk(manifest_rows):
    """Every manifest row MUST point at an existing file.

    Catches "removed a vendored file, left a stale manifest row" —
    which would otherwise pass the on-disk hash check vacuously.
    """
    on_disk = {p.name for p in _vendor_files_on_disk()}
    documented = set(manifest_rows.keys())
    missing_from_disk = documented - on_disk
    assert not missing_from_disk, (
        "These VENDORS.md rows reference files that do NOT exist on "
        f"disk: {sorted(missing_from_disk)}. Either restore the file "
        "or remove the manifest row."
    )


# ---------------------------------------------------------------------------
# SHA-256 integrity — the actual point of this whole module
# ---------------------------------------------------------------------------

def test_every_vendored_file_matches_recorded_sha256(manifest_rows):
    """The on-disk SHA-256 of every vendored file MUST match VENDORS.md.

    This is the integrity check that catches:
      * accidental edits (someone reformatted petite-vue.js),
      * corruption (truncated download committed),
      * supply-chain swaps (someone updated the file but not the hash,
        or the other way around).

    Failures print the actual vs expected hash plus a hint to use the
    update procedure documented at the bottom of VENDORS.md.
    """
    mismatches: list[str] = []
    for filename, expected in manifest_rows.items():
        path = VENDOR_DIR / filename
        if not path.is_file():
            # Covered by test_every_manifest_entry_exists_on_disk.
            continue
        actual = _sha256_of(path)
        if actual != expected:
            mismatches.append(
                f"{filename}: expected {expected}, got {actual}"
            )
    assert not mismatches, (
        "Vendored asset SHA-256 mismatch — file content drifted from "
        "the pinned hash in VENDORS.md:\n  "
        + "\n  ".join(mismatches)
        + "\nIf this was an intentional version bump, follow the "
        "'Updating vendored files' procedure in VENDORS.md."
    )


def test_no_vendored_file_is_empty():
    """Defensive: a 0-byte file would still hash to a fixed value.

    Refuse to count an empty file as "valid" even if a prankster
    populated VENDORS.md with the SHA-256 of the empty string.
    """
    empties = [p.name for p in _vendor_files_on_disk() if p.stat().st_size == 0]
    assert not empties, f"Empty vendored files found: {empties}"
