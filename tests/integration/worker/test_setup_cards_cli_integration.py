# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``run_worker.py --setup-cards`` as its own process (setup-rebuild FR-WK-1, chunk 4 "card probe outside a running worker").

Real parts: the worker program's argument dispatch, stdin and stdout, the
OS card probe of this machine and the worker log, in a child with a scratch
home and settings folder (``_worker_cli``) where no Blender is installed.
The answer's fields one by one are unit cases.
"""
import pytest

from tests.integration._worker_cli import WorkerCli


def test_setup_cards_answers_one_json_object_and_starts_nothing(tmp_path):
    """FR-WK-1: exit 0 and one JSON object with ``cards``; nothing on stderr; only the worker log written.

    The worker's folder holds nothing but ``logs`` afterwards: no Blender was
    fetched or started and no settings were written.
    """
    cli = WorkerCli(tmp_path)

    code, answer, stdout, stderr = cli.run('--setup-cards', {})

    assert code == 0, (stdout, stderr, cli.log_text())
    assert answer is not None and isinstance(answer['cards'], list), stdout
    assert stderr == ''
    assert sorted(entry.name for entry in cli.worker_dir.iterdir()) == ['logs']
    assert 'Setup: listed' in cli.log_text()
    if answer['os_probe_failed']:
        pytest.skip("This machine's operating system card list is not readable here")
    for card in answer['cards']:
        assert card['key'].endswith(card['address']), card  # the text --setup-apply takes in ``cards``
