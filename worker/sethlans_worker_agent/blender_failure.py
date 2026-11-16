# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
How a failed Blender run is reported (#238, #240).

Blender runs the worker's generated settings script with ``--python``. By
default Blender ignores a script that raises or cannot be compiled and
renders anyway, with whatever settings were in place. The worker therefore
passes ``--python-exit-code SETTINGS_SCRIPT_EXIT_CODE`` immediately before
``--python`` (``python_script_args``): a failing settings script stops
Blender before the render with that exit code, and the job fails with a
message naming the failing line and the exception.

The GPU device check (#238) runs before the user settings and leaves
through ``sys.exit(GPU_CHECK_EXIT_CODE)`` (not affected by
``--python-exit-code``) after writing a marker line at column 0. The exit
code decides first: exit ``SETTINGS_SCRIPT_EXIT_CODE`` is always a settings
failure, even when stderr contains the marker (a user's setting value is
echoed in the traceback and could forge it). For every other non-zero code
a stderr line that STARTS with the marker means the GPU check failed
(interpreter shutdown can turn its exit code into another one).

Every detail copied into a job error is made printable: line breaks become
``"; "``, tabs become spaces, and any other non-printable character (ESC,
BEL, bidi controls, lone surrogates) is replaced by its escaped form.
"""
from typing import List, Optional

from sethlans_worker_agent.gpu_device_script import GPU_CHECK_MARKER

# Exit code Blender returns when the settings script fails. Distinct from
# gpu_device_script.GPU_CHECK_EXIT_CODE (87); Blender itself exits with 0,
# 1 or a signal-derived code, never with this one.
SETTINGS_SCRIPT_EXIT_CODE = 86

# Longest detail copied into a job's error message, and longest failing
# source line inside it (so the exception text always fits).
_MAX_DETAIL_CHARS = 1000
_MAX_SOURCE_CHARS = 200
_TRACEBACK_HEAD = 'Traceback (most recent call last):'


def python_script_args(script_path) -> List[str]:
    """Return the Blender arguments that run ``script_path`` and stop on its failure."""
    return ['--python-exit-code', str(SETTINGS_SCRIPT_EXIT_CODE), '--python', str(script_path)]


def _printable(text, limit) -> str:
    """Join lines with "; ", turn tabs into spaces, escape other non-printables, cut to ``limit``."""
    lines = [ln.replace('\t', ' ').strip() for ln in (text or '').splitlines()]
    joined = '; '.join(ln for ln in lines if ln)
    return ''.join(c if c.isprintable() else ascii(c)[1:-1] for c in joined)[:limit]


def _gpu_check_detail(return_code, stderr_text) -> Optional[str]:
    """Return the marker line's detail if this exit is a failed GPU check, else None."""
    if not return_code or return_code == SETTINGS_SCRIPT_EXIT_CODE:
        return None
    for line in (stderr_text or '').splitlines():
        if line.startswith(GPU_CHECK_MARKER):
            return line[len(GPU_CHECK_MARKER):]
    return None


def gpu_check_failure_message(return_code, stderr_text) -> Optional[str]:
    """Return a job error message for a failed device check, else None.

    A failed check is a non-zero exit other than SETTINGS_SCRIPT_EXIT_CODE
    with a stderr line starting with the marker. Without the marker, every
    code (including GPU_CHECK_EXIT_CODE) is a plain failure.
    """
    detail = _gpu_check_detail(return_code, stderr_text)
    if detail is None:
        return None
    return (
        "GPU device check failed: Blender did not enable the "
        "requested GPU, so the render was stopped before it "
        f"started. {_printable(detail, _MAX_DETAIL_CHARS)}"
    )


def _last_lines(lines) -> str:
    return '; '.join(ln.strip() for ln in lines if ln.strip())[-500:] or 'No STDERR.'


def _traceback_tail(stderr_text) -> str:
    """Return the failing source line and the exception from the last traceback.

    Blender prints a normal Python traceback for a failing ``--python``
    script: ``File "...", line 27`` / the indented source line / a caret line /
    ``AttributeError: ...``. A script that cannot be compiled prints the
    ``File`` line, the source line and ``SyntaxError: ...`` without the
    ``Traceback`` header. The source line counts only when it is indented,
    and is cut to ``_MAX_SOURCE_CHARS`` so the exception always fits. Falls
    back to the last lines of stderr.
    """
    lines = [ln.rstrip() for ln in (stderr_text or '').splitlines()]
    start = max((i for i, ln in enumerate(lines) if ln.strip() == _TRACEBACK_HEAD), default=None)
    if start is None:
        start = max((i for i, ln in enumerate(lines) if ln.lstrip().startswith('File "')), default=None)
    if start is None:
        return _last_lines(lines)
    block = lines[start:]
    code = ''
    for i, ln in enumerate(block[:-1]):
        nxt = block[i + 1]
        if ln.lstrip().startswith('File "') and nxt[:1].isspace() and nxt.strip():
            code = nxt.strip()
    exception = next((ln for ln in block[1:] if ln and not ln[0].isspace()), '').strip()
    if not code and not exception:
        return _last_lines(lines)
    if len(code) > _MAX_SOURCE_CHARS:
        code = code[:_MAX_SOURCE_CHARS] + '...'
    return '; '.join(part for part in (code and f"Failing line: {code}", exception) if part)


def settings_script_failure_message(return_code, stderr_text) -> Optional[str]:
    """Return the job error message for a failed settings script, else None."""
    if return_code != SETTINGS_SCRIPT_EXIT_CODE:
        return None
    detail = _printable(_traceback_tail(stderr_text), _MAX_DETAIL_CHARS)
    return (
        "The render settings script failed, so the render was stopped before "
        f"it started. {detail}"
    )


def describe_blender_failure(return_code, stderr_text) -> str:
    """Return the job error message for a non-zero Blender exit."""
    message = (gpu_check_failure_message(return_code, stderr_text)
               or settings_script_failure_message(return_code, stderr_text))
    if message:
        return message
    details = _printable(stderr_text, 500) or "No STDERR."
    return f"Blender exited with code {return_code}. Details: {details}"


def request_recheck_if_check_failed(return_code, stderr_text) -> bool:
    """Ask for an immediate GPU list comparison after a failed device check.

    Called from the job thread when Blender exits non-zero. If the exit was
    the GPU device check (same rule as ``gpu_check_failure_message``), the
    card list this worker uses may be stale, so the agent loop is asked to
    compare it with a fresh detection on its next iteration (#239). Only
    sets a threading.Event; the detection itself runs in the agent loop
    thread. Returns True when the request was made.
    """
    if _gpu_check_detail(return_code, stderr_text) is None:
        return False
    from sethlans_worker_agent.capacity import recheck
    recheck.request_gpu_list_recheck()
    return True
