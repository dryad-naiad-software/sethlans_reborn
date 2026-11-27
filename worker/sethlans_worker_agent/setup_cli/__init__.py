# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker program's one-shot setup commands (setup-rebuild FR-WK-1).

``run_worker --setup-cards | --setup-discover | --setup-apply``: each reads
one JSON object on stdin (at most 64 KiB), prints exactly one JSON object on
stdout and logs only to the worker log. Exit codes:

- 0: success, the command's answer.
- 1: an expected failure, ``{"error": <code>, "message": <text>}``; codes:
  ``bad_input``, ``config_unreadable``, ``card_held``, ``card_changed`` (the
  worker page saved a card choice while ``--setup-apply`` ran; nothing
  saved), ``discovery_failed``, ``save_failed`` and the enrollment ones (``invalid_key``, ``unreachable``,
  ``certificate_mismatch``, ``rate_limited``, ``manager_not_ready``,
  ``invalid_hostname``, ``enrollment_failed``).
- 2: a usage error (``usage_error``: other arguments) or an internal error
  (``internal_error``, details in the worker log), in the same shape.

None of them starts the agent loop, Waitress or Caddy. Secrets come only on
stdin (setup-rebuild FR-WK-2). While a command runs, ``sys.stdout`` points at
stderr, so a stray ``print`` cannot break the one-object answer.
"""
import logging
import sys

COMMANDS = ('--setup-cards', '--setup-discover', '--setup-apply')
USAGE = 'usage: run_worker --setup-cards | --setup-discover | --setup-apply  (one JSON object on stdin)'

logger = logging.getLogger(__name__)


def _handler(command: str):
    if command == '--setup-cards':
        from sethlans_worker_agent.setup_cli import cards_cmd
        return cards_cmd.run
    if command == '--setup-discover':
        from sethlans_worker_agent.setup_cli import discover_cmd
        return discover_cmd.run
    from sethlans_worker_agent.setup_cli import apply_cmd
    return apply_cmd.run


def _answer(stdin, command: str):
    """``(exit code, answer)`` for one command."""
    from sethlans_worker_agent.setup_cli.stdio import CliError, read_request
    try:
        return 0, _handler(command)(read_request(stdin))
    except CliError as failure:
        logger.info("Setup command %s refused: %s", command, failure.code)
        return 1, {'error': failure.code, 'message': failure.message}
    except Exception:  # noqa: BLE001 - the one-object contract holds for any failure
        logger.exception("Setup command %s failed.", command)
        return 2, {'error': 'internal_error', 'message': 'The setup command failed; see the worker log.'}


def main(argv) -> int:
    """Run ``argv[0]`` (one of ``COMMANDS``, nothing after it); the exit code."""
    from sethlans_worker_agent.setup_cli.stdio import binary_streams, log_to_worker_log, write_answer
    stdin, stdout = binary_streams()
    if stdout is None:
        return 2  # nowhere to answer
    sys.stdout = sys.stderr
    if len(argv) != 1 or argv[0] not in COMMANDS:
        write_answer(stdout, {'error': 'usage_error', 'message': USAGE})
        return 2
    log_to_worker_log()
    code, answer = _answer(stdin, argv[0])
    write_answer(stdout, answer)
    return code
