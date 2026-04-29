# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Subprocess streamer + Ctrl+C propagation for the dev-script harness.

Extracted from :mod:`tools._dev_common` (issue #176) when the layered
SIGINT/SIGTERM/SIGKILL escalation pushed it over the 300-line ceiling;
:func:`stream_subprocess` is re-exported by ``_dev_common``.

POSIX: child gets ``start_new_session=True``; we ``os.killpg`` with
SIGINT. Windows: child gets ``CREATE_NEW_PROCESS_GROUP``; we
``send_signal(CTRL_BREAK_EVENT)`` (SIGINT isn't deliverable
cross-process on Windows). Escalation cascade with status messages:
SIGINT/CTRL_BREAK + 10s -> SIGTERM + 3s -> SIGKILL + 2s.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Mapping, Optional, Sequence


# Issue #176 layer 2 — escalation timings for child-process shutdown.
# A SIGINT to the child should normally trigger its own polite-shutdown
# path (e.g. the wizard's signal handler unblocks server.run() in ~1s).
# We allow 10 s before falling back to SIGTERM, then 3 s before SIGKILL.
_CHILD_SIGINT_GRACE_SECONDS = 10.0
_CHILD_TERM_GRACE_SECONDS = 3.0
_CHILD_KILL_GRACE_SECONDS = 2.0


def _stream_pipe(pipe, prefix: str, dest) -> None:
    """Background pump: forward *pipe* lines to *dest* with *prefix*."""
    try:
        for raw_line in iter(pipe.readline, b""):
            try:
                line = raw_line.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — defensive
                line = repr(raw_line)
            dest.write(f"{prefix} {line}")
            dest.flush()
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _send_sigint_to_child(proc: "subprocess.Popen[bytes]") -> bool:
    """Forward SIGINT/CTRL_BREAK_EVENT to *proc*. Returns True on success.

    POSIX: ``os.killpg`` against the child's process group (requires
    ``start_new_session=True`` at spawn). Windows: ``CTRL_BREAK_EVENT``
    via ``proc.send_signal`` (requires ``CREATE_NEW_PROCESS_GROUP``);
    SIGINT is unreliable cross-process on Windows.
    """
    try:
        if os.name == "nt":
            # CTRL_BREAK_EVENT is the only reliable cross-process signal
            # on Windows. The child must have been spawned with
            # CREATE_NEW_PROCESS_GROUP; otherwise the call raises OSError.
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            return True
        # POSIX: kill the child's session/process group so any
        # grandchildren get the signal too.
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = proc.pid
        os.killpg(pgid, signal.SIGINT)
        return True
    except (OSError, ValueError):
        return False


def _say(prefix: str, msg: str) -> None:
    """Write *msg* to stderr with *prefix* and flush. Internal helper."""
    sys.stderr.write(f"{prefix} {msg}\n")
    sys.stderr.flush()


def _wait_or_none(
    proc: "subprocess.Popen[bytes]", timeout: float,
) -> Optional[int]:
    """Wait for *proc* up to *timeout*; return rc or None on timeout."""
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _try_sigint(
    proc: "subprocess.Popen[bytes]", prefix: str,
) -> Optional[int]:
    """Send SIGINT/CTRL_BREAK + wait. Return rc on exit, None on timeout."""
    if not _send_sigint_to_child(proc):
        _say(prefix, "could not forward SIGINT; escalating immediately")
        return None
    return _wait_or_none(proc, _CHILD_SIGINT_GRACE_SECONDS)


def _try_terminate(
    proc: "subprocess.Popen[bytes]", prefix: str,
) -> Optional[int]:
    """``terminate()`` + wait. Return rc on exit, None on timeout."""
    _say(
        prefix,
        f"did not exit on SIGINT; sending SIGTERM "
        f"(grace {_CHILD_TERM_GRACE_SECONDS:.0f}s)",
    )
    try:
        proc.terminate()
    except OSError:
        pass
    return _wait_or_none(proc, _CHILD_TERM_GRACE_SECONDS)


def _try_kill(proc: "subprocess.Popen[bytes]", prefix: str) -> int:
    """``kill()`` + wait. Returns rc, or -9 if even SIGKILL doesn't take."""
    _say(prefix, "still alive; sending SIGKILL")
    try:
        proc.kill()
    except OSError:
        pass
    rc = _wait_or_none(proc, _CHILD_KILL_GRACE_SECONDS)
    return rc if rc is not None else -9


def _terminate_child(proc: "subprocess.Popen[bytes]", prefix: str) -> int:
    """SIGINT -> SIGTERM -> SIGKILL cascade with status messages.

    Issue #176 layer 2: forward Ctrl+C cleanly so the child runs its
    own polite-shutdown path (e.g. wizard's signal handler), waiting
    up to :data:`_CHILD_SIGINT_GRACE_SECONDS`. Falls back to
    ``terminate()`` then ``kill()``. Each escalation prints its own
    status line so the operator isn't left wondering.
    """
    _say(
        prefix,
        f"\ncaught Ctrl+C; forwarding SIGINT to child "
        f"(waiting up to {_CHILD_SIGINT_GRACE_SECONDS:.0f}s)",
    )
    rc = _try_sigint(proc, prefix)
    if rc is not None:
        return rc
    rc = _try_terminate(proc, prefix)
    if rc is not None:
        return rc
    return _try_kill(proc, prefix)


def _popen_kwargs_for_signal_propagation() -> dict:
    """Spawn-kwargs the child needs so SIGINT/CTRL_BREAK reaches it.

    POSIX: ``start_new_session=True`` puts the child in its own session
    + process group so we can ``killpg`` it without also killing this
    parent. Windows: ``CREATE_NEW_PROCESS_GROUP`` so we can later send
    ``CTRL_BREAK_EVENT`` (the cross-process equivalent of SIGINT).
    """
    if os.name == "nt":
        return {
            "creationflags": getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200,
            ),
        }
    return {"start_new_session": True}


def _install_break_handler_windows() -> None:
    """On Windows, route SIGBREAK to a KeyboardInterrupt path.

    When the dev_*.py harness is spawned with
    ``CREATE_NEW_PROCESS_GROUP`` (e.g. by a parent harness, or by
    the user's terminal hitting Ctrl+Break) Python sees CTRL_BREAK
    as :data:`signal.SIGBREAK` rather than SIGINT. The default
    behaviour terminates the process without raising
    KeyboardInterrupt, so the streamer's ``except`` never runs and
    the child is orphaned.

    Installing this handler BEFORE we spawn the child means there's
    no race window where a CTRL_BREAK could slip through.
    """
    if os.name != "nt":
        return
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is None:
        return

    def _handler(signum, _frame):
        raise KeyboardInterrupt(f"SIGBREAK ({signum}) received")

    try:
        signal.signal(sigbreak, _handler)
    except (ValueError, OSError):
        pass


def stream_subprocess(
    cmd: Sequence[str],
    *,
    prefix: str,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
) -> int:
    """Run *cmd*, prefix each output line with *prefix*, return rc.

    Stdout + stderr are line-merged into the parent's streams. Ctrl+C
    (issue #176) is propagated to the child via SIGINT (POSIX
    process-group) / CTRL_BREAK_EVENT (Windows process group), with
    a 10 s -> 3 s -> SIGKILL escalation cascade.

    On Windows, we install a SIGBREAK handler so ``CTRL_BREAK_EVENT``
    delivered to the dev script's own process group raises
    KeyboardInterrupt (default Windows behaviour is hard-terminate).
    """
    # Install SIGBREAK handler BEFORE spawning so there's no race
    # window where a CTRL_BREAK could slip past the default terminator.
    _install_break_handler_windows()

    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)

    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=proc_env,
        cwd=str(cwd) if cwd else None,
        bufsize=0,
        **_popen_kwargs_for_signal_propagation(),
    )

    threads = [
        threading.Thread(
            target=_stream_pipe, args=(proc.stdout, prefix, sys.stdout),
            daemon=True,
        ),
        threading.Thread(
            target=_stream_pipe, args=(proc.stderr, prefix, sys.stderr),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()

    rc = _wait_with_signal_polling(proc, prefix)

    for t in threads:
        t.join(timeout=2.0)

    return rc if rc is not None else 1


# Issue #176 — Windows quirk: ``subprocess.wait()`` uses a non-alertable
# WaitForSingleObject under the hood, so a signal handler that raises
# KeyboardInterrupt can sit queued in the interpreter until the wait
# returns naturally. Polling with a short timeout lets the bytecode
# boundary check run and surface the pending KeyboardInterrupt.
_WAIT_POLL_SECONDS = 0.5


def _wait_with_signal_polling(
    proc: "subprocess.Popen[bytes]", prefix: str,
) -> Optional[int]:
    """Block on *proc* but interruptible on signal-handler-raised exits."""
    while True:
        try:
            return proc.wait(timeout=_WAIT_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            # No progress — loop. Bytecode boundary in this branch
            # gives Python a chance to deliver any pending signal.
            continue
        except KeyboardInterrupt:
            # Layered escalation. Swallow KeyboardInterrupt — re-raising
            # would race the streamer threads' final flush of the
            # child's shutdown log lines.
            return _terminate_child(proc, prefix)


__all__ = [
    "stream_subprocess",
]
