# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The adapter Unreal says it chose, against the card the worker asked for
(card selection FR-CU-3, B5, C5; chunk 7 security review).

``AdapterWatch`` reads each new log line of a warm-up or render launch in
the runner's line monitor (``runner_state``). Only whole lines count
(``pilot.FOUND_ADAPTER`` and ``pilot.CHOSEN_ADAPTER``: anchored at the line
start after UE's optional ``[time][frame]`` prefix, one or two digits), so an
ini value UE echoes inside another line cannot match. The first ``Chosen
D3D12 Adapter Id = M`` is a mismatch when ``M`` differs from ``N`` of
``-graphicsadapter=N``, or when the ``Found D3D12 adapter M:`` line names
other PCI vendor or device ids (or revision, when both sides have it) than
the chosen card. ``line()`` is then true and the runner stops the tree. The
ids check cannot tell two identical cards apart (nor a virtual adapter that
copies a real card's ids); it only catches a different model.

``seen(outcome, expected)`` names what to report: the mismatch text; or
``'unknown'`` when the launch exited by itself without the line although its
log has ``Found D3D12 adapter`` lines (B5); else ``None`` (no adapter lines,
or a run the worker ended itself: an ordinary failure, Unreal stays on).

A project's own ini can still forge whole lines (quoted ini values unescape
``\\n``). So ``report`` (Unreal job thread) blames the project first: the
``adapter_mismatch`` alert names it (C5: a failed write is logged), the
project is refused on this worker until restart (``refused``: the reserver
declines its jobs, ``job_run`` refuses one before any launch), and Unreal is
disabled until restart only when a second, different project also
mismatches: a real index fault shows on every project. ``_lock`` is a leaf.
"""
import logging
import threading
from typing import Callable, Dict, Optional, Tuple

from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict
from sethlans_worker_agent.unreal_executor.pilot import CHOSEN_ADAPTER, FOUND_ADAPTER, FOUND_REVISION

logger = logging.getLogger(__name__)

MISMATCH = 'adapter_mismatch'      # the runner's reason, and the alert kind (FR-CK-5)
UNKNOWN = 'unknown'
DISABLED_REASON = "Unreal chose another graphics card than the ticked one, for two different projects"
PROJECT_CHARS = 12

_lock = threading.Lock()
_refused: Dict[str, str] = {}      # archive sha256 -> what was seen


def ids_of(choice) -> Optional[Tuple]:
    """``(vendor_id, device_id, revision_id)`` of a chosen card, or ``None`` without ids."""
    if choice is None or getattr(choice, 'vendor_id', None) is None or getattr(choice, 'device_id', None) is None:
        return None
    return choice.vendor_id, choice.device_id, getattr(choice, 'revision_id', None)


def _found_ids(match, text: str) -> Tuple:
    revision = FOUND_REVISION.search(text, match.end())
    return (int(match.group(3), 16), int(match.group(4), 16) if match.group(4) else None,
            int(revision.group(1), 16) if revision else None)


def _same(found: Tuple, expected: Tuple) -> bool:
    if found[1] is None:
        return True
    return found[:2] == expected[:2] and (found[2] is None or expected[2] is None or found[2] == expected[2])


class AdapterWatch:
    """The adapter lines of one launch; ``expected`` is ``None`` for a launch without ``-graphicsadapter``."""

    def __init__(self, expected: Optional[int], expected_ids: Optional[Tuple] = None):
        self.expected, self.expected_ids = expected, expected_ids
        self.chosen: Optional[int] = None
        self.found = False
        self.found_ids: Dict[int, Tuple] = {}
        self.problem = ''

    def line(self, text: str) -> bool:
        """Read one log line; true when it is the ``Chosen`` line and the choice is not the expected card."""
        if self.expected is None or self.chosen is not None:
            return False
        match = FOUND_ADAPTER.search(text)
        if match is not None:
            self.found = True
            self.found_ids.setdefault(int(match.group(1)), _found_ids(match, text))
            return False
        match = CHOSEN_ADAPTER.search(text)
        if match is None:
            return False
        self.chosen = int(match.group(1))
        self.problem = self._check()
        return bool(self.problem)

    def _check(self) -> str:
        if self.chosen != self.expected:
            return str(self.chosen)
        found = self.found_ids.get(self.chosen)
        if found is None or self.expected_ids is None or _same(found, self.expected_ids):
            return ''
        return f"{self.chosen} (PCI ids {found[0]:04x}:{found[1]:04x}, not the ticked card)"

    @property
    def mismatch(self) -> bool:
        return bool(self.problem)


def seen(outcome, expected: Optional[int]) -> Optional[str]:
    """The adapter to report as chosen instead of ``expected``, or ``None`` when there is nothing to report."""
    if expected is None:
        return None
    problem = getattr(outcome, 'adapter_problem', '')
    if problem:
        return problem
    chosen = getattr(outcome, 'adapter_chosen', None)
    if chosen is not None:
        return None if chosen == expected else str(chosen)
    if getattr(outcome, 'adapter_found', False) and outcome.reason == 'exited':
        return UNKNOWN
    return None


def project_key(params) -> str:
    """What ran: ``<archive sha256>:<overlay sha256 or ''>`` (an overlay replaces ``Config/**``, so another
    overlay on the same archive is another project here, and cannot get the archive's owner refused)."""
    return f"{params.archive_sha256}:{params.overlay_sha256 or ''}"


def _label(project: str) -> str:
    archive, _sep, overlay = project.partition(':')
    return f"{archive[:PROJECT_CHARS]}+{overlay[:PROJECT_CHARS] or 'none'}"


def refused(project: str) -> bool:
    """True when a launch of this project (``project_key``) reported another card on this worker."""
    with _lock:
        return project in _refused


def _refuse(project: str, adapter: str) -> bool:
    """Record the project; true when another project was refused before (a fault of the worker, not the project)."""
    with _lock:
        _refused.setdefault(project, adapter)
        return len(_refused) >= 2


def _add_alert(kind, key, name, detail):
    from sethlans_worker_agent.cards import store
    return store.add_alert(kind, key, name, detail)


def report(adapter: str, choice, project: str, disable: Callable[[str], None],
           add_alert: Optional[Callable] = None) -> LocalConflict:
    """Alert, refuse the project, disable Unreal on the second project; the ``LocalConflict`` that releases the job."""
    second = _refuse(project, adapter)
    detail = f"Unreal chose adapter {adapter} instead of {choice.index}"
    logger.critical("Unreal job %s: %s (%s, %s; project %s).", choice.job_id, detail, ascii(choice.name), choice.key,
                    project)
    try:
        (add_alert or _add_alert)(MISMATCH, choice.key, choice.name, f"{detail}; project {_label(project)}")
    except Exception:  # noqa: BLE001 - C5: the job is still released
        logger.exception("The %s alert for %s could not be saved.", MISMATCH, choice.key)
    if second:
        disable(DISABLED_REASON)
    else:
        logger.error("Unreal jobs of project %s are refused on this worker until restart.", project)
    return LocalConflict('local_conflict', f"{detail}; the job goes back to the queue.")


def check_run(outcome, choice, runner) -> Optional[LocalConflict]:
    """After one launch: the ``LocalConflict`` to raise for an adapter mismatch (alert and refusal done), or None."""
    adapter = seen(outcome, choice.index if choice is not None else None)
    if not adapter:
        return None
    return report(adapter, choice, getattr(runner, 'project', '') or 'unknown', runner.disable_until_restart,
                  runner.add_alert)


def reset_for_tests() -> None:
    with _lock:
        _refused.clear()
