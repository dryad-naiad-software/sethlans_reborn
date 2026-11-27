# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A stand-in for ``run_worker --setup-cards | --setup-discover | --setup-apply``
in the setup page browser tests (setup-rebuild chunk 5).

Usage: ``python _fake_worker_cli.py <folder> <command>`` with one JSON object
on stdin, as setup mode's ``worker_cli`` runs the real program. ``<folder>``
holds ``script.json``: for ``--setup-cards`` and ``--setup-discover`` one
``[exit code, answer]``; for ``--setup-apply`` a list of them, used in turn
(the last one repeats). Each ``--setup-apply`` request is recorded as
``apply-<n>.json`` in the folder. The answers follow the worker's own
contract (``worker/sethlans_worker_agent/setup_cli``). Standard library only.
"""
import json
import sys
from pathlib import Path


def main(argv) -> int:
    folder, command = Path(argv[1]), argv[2]
    request = json.loads(sys.stdin.buffer.read().decode('utf-8') or '{}')
    script = json.loads((folder / 'script.json').read_text(encoding='utf-8'))
    if command == '--setup-apply':
        turn = len(list(folder.glob('apply-*.json')))
        (folder / f'apply-{turn}.json').write_text(json.dumps(request), encoding='utf-8')
        answers = script[command]
        code, answer = answers[min(turn, len(answers) - 1)]
    else:
        code, answer = script[command]
    sys.stdout.write(json.dumps(answer))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
