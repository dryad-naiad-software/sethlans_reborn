# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Render output collection (Unreal foundation FR-UE-13, FR-UE-25, FR-PA-21;
D5).

The job's ``j/<...>/output/`` is scanned **recursively** without following
links (``fs_safe.walk_tree`` raises on any reparse point or special file).
PNG files are matched by extension, case-insensitively; other files are
ignored and logged.

* normal job: exactly one PNG, directly in ``output/``; otherwise
  "Unreal render produced no PNG output." / "... <n> PNG files; expected
  1." / "... wrote output to a subdirectory (<relpath>); preset path tokens
  leaked through the override.";
* pilot: exactly one PNG per pilot frame, named ``sethlans_<id>.<frame>``
  (frame zero-padded to four digits), directly in ``output/``.
"""
import logging
from pathlib import Path
from typing import List, Sequence

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure

logger = logging.getLogger(__name__)


def _pngs(output_dir: Path) -> List[str]:
    try:
        listing = fs_safe.walk_tree(output_dir)
    except OSError as exc:
        raise JobFailure(f"Unreal render output is unsafe ({exc}).") from exc
    pngs = sorted(rel for rel in listing.files if rel.lower().endswith('.png'))
    others = sorted(rel for rel in listing.files if rel not in pngs)
    if others:
        logger.info("Ignoring non-PNG render output: %s", ', '.join(others[:20]))
    return pngs


def collect_single(output_dir) -> Path:
    output_dir = Path(output_dir)
    pngs = _pngs(output_dir)
    if not pngs:
        raise JobFailure("Unreal render produced no PNG output.")
    if len(pngs) > 1:
        raise JobFailure(f"Unreal render produced {len(pngs)} PNG files; expected 1.")
    if '/' in pngs[0]:
        raise JobFailure(f"Unreal render wrote output to a subdirectory ({pngs[0]}); preset path tokens "
                         "leaked through the override.")
    return output_dir / pngs[0]


def pilot_name(job_id: int, frame: int) -> str:
    return f"sethlans_{int(job_id)}.{int(frame):04d}.png"


def collect_pilot(output_dir, job_id: int, frames: Sequence[int]) -> List[Path]:
    output_dir = Path(output_dir)
    found = {rel.lower() for rel in _pngs(output_dir)}
    expected = {pilot_name(job_id, f).lower() for f in frames}
    missing = sorted(expected - found)
    extra = sorted(found - expected)
    if missing or extra:
        raise JobFailure(f"the render wrote {len(found)} PNG files for {len(expected)} pilot frames "
                         f"(missing {missing[:3]}, unexpected {extra[:3]}).")
    return [output_dir / pilot_name(job_id, f) for f in frames]
