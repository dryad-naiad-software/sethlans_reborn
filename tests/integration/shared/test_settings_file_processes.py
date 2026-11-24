# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Two processes: one rewrites the settings file in a loop, one reads it.

On Windows a reader that opens the file while the writer's ``os.replace``
runs gets ``PermissionError``; both sides retry briefly, so no read and
no write may fail, and every read sees one whole version of the file.
"""
import os
import subprocess
import sys
from pathlib import Path

from shared import settings_file

REPO_ROOT = Path(__file__).resolve().parents[3]
WRITES = 300

WRITER = """
import sys
from shared import settings_file
path = sys.argv[1]
for i in range(int(sys.argv[2])):
    settings_file.set_values("server", {"port": str(9000 + i % 2), "pad": "x" * (i % 50)}, path)
print("done")
"""


def test_reader_never_fails_while_another_process_writes(tmp_path):
    ini = tmp_path / "sethlans.ini"
    settings_file.set_values("server", {"port": "9000"}, ini)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    # Output goes to files, not pipes: a full pipe would block the writer
    # while this process is busy reading in its loop.
    out_path, err_path = tmp_path / "writer.out", tmp_path / "writer.err"
    with open(out_path, "w") as out_fh, open(err_path, "w") as err_fh:
        writer = subprocess.Popen(
            [sys.executable, "-c", WRITER, str(ini), str(WRITES)],
            cwd=str(REPO_ROOT), env=env, stdout=out_fh, stderr=err_fh,
        )
    reads, failures = 0, []
    try:
        while writer.poll() is None:
            try:
                port = settings_file.read_settings(ini).get("server", "port")
            except Exception as exc:  # noqa: BLE001 - every failure is the finding
                failures.append(repr(exc))
                continue
            reads += 1
            if port not in ("9000", "9001"):
                failures.append(f"torn read: port={port!r}")
        writer.wait(timeout=120)
    finally:
        if writer.poll() is None:
            writer.kill()
            writer.wait()
    err = err_path.read_text()
    assert writer.returncode == 0, err
    assert out_path.read_text().strip() == "done"
    assert "Traceback" not in err
    assert failures == [], failures[:5]
    assert reads > 0
