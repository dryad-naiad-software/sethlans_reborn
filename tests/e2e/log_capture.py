# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Temp-file-based log capture for E2E subprocesses.

Using files instead of subprocess.PIPE avoids a deadlock on Windows
where a full pipe buffer (4 KB) blocks the subprocess when nobody is
reading from it.
"""

import os
import tempfile


def open_log_files(prefix):
    """Open temp files for capturing subprocess stdout and stderr.

    Returns:
        tuple: (stdout_file, stderr_file) -- open file objects.
    """
    stdout_f = tempfile.NamedTemporaryFile(
        mode="w", prefix=f"e2e_{prefix}_out_",
        suffix=".log", delete=False,
    )
    stderr_f = tempfile.NamedTemporaryFile(
        mode="w", prefix=f"e2e_{prefix}_err_",
        suffix=".log", delete=False,
    )
    return stdout_f, stderr_f


def _close_and_read(file_obj):
    """Close a temp log file, read its contents, and delete it."""
    path = file_obj.name
    try:
        file_obj.close()
    except Exception:
        pass
    try:
        with open(path, "r", errors="replace") as fh:
            content = fh.read()
    except Exception:
        content = ""
    try:
        os.remove(path)
    except OSError:
        pass
    return content


def read_log_files(proc):
    """Read and clean up the temp log files attached to a process."""
    log_files = getattr(proc, '_log_files', None)
    if not log_files:
        return "", ""
    stdout_f, stderr_f = log_files
    return _close_and_read(stdout_f), _close_and_read(stderr_f)


def peek_log_files(proc):
    """Read log files without closing them (safe while process runs)."""
    log_files = getattr(proc, '_log_files', None)
    if not log_files:
        return "", ""
    stdout_f, stderr_f = log_files
    contents = []
    for f in (stdout_f, stderr_f):
        try:
            # Flush parent's write buffer, then read from disk.
            f.flush()
            with open(f.name, "r", errors="replace") as fh:
                contents.append(fh.read())
        except Exception:
            contents.append("")
    return contents[0], contents[1]
