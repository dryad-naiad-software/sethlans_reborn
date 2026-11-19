# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Project text that could fake UE's D3D12 adapter log lines (card selection
FR-CU-3; chunk 7 security review).

UE echoes ini values into its log (``LogConfig: Set CVar [[...]]``) and
quoted ini values unescape ``\\n``, so a project ini can write a whole line
``LogD3D12RHI: Chosen D3D12 Adapter Id = 7`` before the real one. No real
project needs the text ``D3D12 Adapter`` in its config, so a project whose
``*.ini`` files, ``.uproject`` or ``.uplugin`` files contain it (any case;
JSON files also after decoding, so ``\\u`` escapes cannot hide it) is refused
before every launch, through the Zen/DDC guard's scan
(``zen_guard.scan_project_config``). Overlay files are in the working copy
and count the same. The refusal is a job failure naming the file; it is not
an adapter mismatch and never disables Unreal.
"""
import json
import re
from pathlib import Path
from typing import Callable, Iterator, Optional

# UE's ini reader turns a backslash + line break (and any line break inside {...}) into one space before
# parsing, so whitespace and backslashes between the words count as the space (security review).
_MARKER_TEXT = re.compile(r'd3d12[\s\\]*adapter', re.IGNORECASE)
JSON_SUFFIXES = ('.uproject', '.uplugin')


def _message(relpath: str) -> str:
    return (f"Project file {relpath} contains the text 'D3D12 Adapter', which could fake Unreal's graphics card log "
            "lines; farm renders refuse it.")


def check_text(text: str, relpath: str) -> str:
    """The refusal message when ``text`` (a decoded project file) contains the marker, else ''."""
    return _message(relpath) if _MARKER_TEXT.search(text) else ''


def _strings(value) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def check_json_text(text: str, relpath: str) -> str:
    """As ``check_text``, and over every key and string of the decoded JSON."""
    found = check_text(text, relpath)
    if found:
        return found
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return ''  # UE cannot load it either; the raw text was checked
    return _message(relpath) if any(_MARKER_TEXT.search(item) for item in _strings(data)) else ''


def check_json_files(project_dir: Path, files, read: Callable[[Path, str], tuple], max_bytes: int) -> Optional[str]:
    """The first refusal over the ``.uproject``/``.uplugin`` files (``files``: relpath -> stat); ``read`` is the
    guard's reader (UTF-8, or UTF-16 with a BOM; fails closed)."""
    for rel in sorted(rel for rel in files if rel.lower().endswith(JSON_SUFFIXES)):
        if files[rel].st_size > max_bytes:
            return f"Project file {rel} is too large to check."
        text, error = read(project_dir.joinpath(*rel.split('/')), rel)
        if error:
            return error
        found = check_json_text(text, rel)
        if found:
            return found
    return None
