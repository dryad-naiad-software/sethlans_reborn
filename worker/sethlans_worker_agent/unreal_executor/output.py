# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Render output collection (Unreal foundation FR-UE-13, FR-UE-25, FR-PA-21;
D5; sequence rendering FR-SW-7, D5, D5b).

The job's ``j/<...>/output/`` is scanned **recursively** without following
links (``fs_safe.walk_tree`` raises on any reparse point or special file).
Files are matched by extension, case-insensitively; other files are
ignored and logged.

* normal job: exactly one PNG, directly in ``output/``; otherwise
  "Unreal render produced no PNG output." / "... <n> PNG files; expected
  1." / "... wrote output to a subdirectory (<relpath>); preset path tokens
  leaked through the override.";
* pilot: exactly one PNG per pilot frame, ``sethlans_<id>.<frame>.png``;
* piece (``collect_range``): exactly ``sethlans_<id>.<n>.<ext>`` for every
  ``n`` in ``[start - lead, end]``, nothing else of that extension; missing
  or extra files fail naming up to 3 of each. The lead-in files are deleted
  after the check; the kept ones come in frame order with their mtimes.

Frame files lie directly in ``output/``; frame numbers have 4 to 7 digits
(zero-pad at least 4, D5b; long edits, D5); a frame under two spellings
(``0070`` and ``00070``) counts as an extra file.
"""
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure

logger = logging.getLogger(__name__)

_FRAME_FILE = re.compile(r'sethlans_(\d{1,12})\.(\d{4,7})\.([a-z]{3})', re.ASCII)


def _files(output_dir: Path, ext: str) -> List[str]:
    try:
        listing = fs_safe.walk_tree(output_dir)
    except OSError as exc:
        logger.error("Unreal render output under %s is unsafe: %s", output_dir, exc)  # paths stay in the local log
        raise JobFailure(f"Unreal render output is unsafe ({type(exc).__name__}).") from exc
    found = sorted(rel for rel in listing.files if rel.lower().endswith('.' + ext))
    others = sorted(rel for rel in listing.files if rel not in found)
    if others:
        logger.info("Ignoring other render output: %s", ', '.join(others[:20]))
    return found


def collect_single(output_dir) -> Path:
    output_dir = Path(output_dir)
    pngs = _files(output_dir, 'png')
    if not pngs:
        raise JobFailure("Unreal render produced no PNG output.")
    if len(pngs) > 1:
        raise JobFailure(f"Unreal render produced {len(pngs)} PNG files; expected 1.")
    if '/' in pngs[0]:
        raise JobFailure(f"Unreal render wrote output to a subdirectory ({pngs[0]}); preset path tokens "
                         "leaked through the override.")
    return output_dir / pngs[0]


def pilot_name(job_id: int, frame: int) -> str:
    return frame_name(job_id, frame, 'png')


def frame_name(job_id: int, frame: int, ext: str) -> str:
    return f"sethlans_{int(job_id)}.{int(frame):04d}.{ext}"


def match_frames(names: Sequence[str], job_id: int, frames: Sequence[int], ext: str) -> Tuple[Dict[int, str], list]:
    """``({frame: name}, extra)`` for file names directly in ``output/``; ``extra`` is every other name."""
    wanted, found, extra = set(frames), {}, []
    for name in names:
        match = _FRAME_FILE.fullmatch(name.lower())
        frame = int(match.group(2)) if match else None
        if match is None or int(match.group(1)) != int(job_id) or match.group(3) != ext or frame not in wanted \
                or frame in found:
            extra.append(name)
        else:
            found[frame] = name
    return found, extra


def _expected(output_dir: Path, job_id: int, frames: Sequence[int], ext: str, what: str) -> Dict[int, str]:
    found, extra = match_frames(_files(output_dir, ext), job_id, frames, ext)
    missing = [frame_name(job_id, f, ext) for f in frames if f not in found]
    if missing or extra:
        raise JobFailure(f"the render wrote {len(found) + len(extra)} {ext.upper()} files for {len(frames)} {what} "
                         f"(missing {missing[:3]}, unexpected {sorted(extra)[:3]}).")
    return found


def collect_pilot(output_dir, job_id: int, frames: Sequence[int]) -> List[Path]:
    output_dir = Path(output_dir)
    found = _expected(output_dir, job_id, list(frames), 'png', 'pilot frames')
    return [output_dir / found[f] for f in frames]


def range_complete(output_dir, job_id: int, first: int, end: int, ext: str) -> bool:
    """FR-SW-8: whether exactly the frames ``first..end`` are there (nothing deleted, nothing raised)."""
    try:
        _expected(Path(output_dir), job_id, range(first, end + 1), ext, 'frames')
    except JobFailure:
        return False
    return True


@dataclass(frozen=True)
class RangeOutput:
    kept: Tuple[Tuple[int, Path, float], ...]   # (frame, path, mtime) of the kept frames, in frame order
    lead_mtimes: Tuple[float, ...]              # the lead-in files' modification times, in frame order


def _mtime(path: Path) -> float:
    return fs_safe.lstat(path).st_mtime


def collect_range(output_dir, job_id: int, start: int, end: int, lead: int, ext: str) -> RangeOutput:
    """FR-SW-7: check every rendered frame, delete the lead-in ones, return the kept ones in frame order."""
    output_dir = Path(output_dir)
    frames = list(range(start - lead, end + 1))
    found = _expected(output_dir, job_id, frames, ext, 'frames')
    try:
        lead_mtimes = tuple(_mtime(output_dir / found[f]) for f in frames[:lead])
        kept = tuple((f, output_dir / found[f], _mtime(output_dir / found[f])) for f in frames[lead:])
    except OSError as exc:
        logger.error("Unreal render output could not be read: %s", exc)
        raise JobFailure(f"Unreal render output could not be read ({type(exc).__name__}).") from exc
    for frame in frames[:lead]:  # rendered only so the kept frames start clean (FR-SP-4); never uploaded
        try:
            os.remove(fs_safe.long_path(output_dir / found[frame]))
        except OSError as exc:
            logger.warning("Could not delete the lead-in frame %s: %s", found[frame], exc)
    return RangeOutput(kept=kept, lead_mtimes=lead_mtimes)
