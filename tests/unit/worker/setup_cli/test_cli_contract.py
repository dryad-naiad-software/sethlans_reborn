# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The stdin/stdout contract of ``run_worker --setup-*`` (setup-rebuild FR-WK-1).

``setup_cli.main`` runs with byte streams in memory (``stdio.binary_streams``
replaced), the command handler replaced by a fake and the worker-log setup
replaced by a no-op, so nothing touches the real streams, handlers or files.
``sys.stdout`` is put back after each run: ``main`` points it at stderr.
"""
import io
import json
import logging
import sys

import pytest

from sethlans_worker_agent import setup_cli
from sethlans_worker_agent.setup_cli import stdio


@pytest.fixture
def run_cli(monkeypatch):
    """``run(argv, stdin_bytes, handler)`` -> ``(exit code, stdout bytes, requests the handler got)``."""
    monkeypatch.setattr(stdio, 'log_to_worker_log', lambda: None)

    def run(argv, stdin_bytes=b'{}', handler=None):
        seen = []

        def fake(request):
            seen.append(request)
            return handler(request) if handler else {'ok': True}
        out = io.BytesIO()
        monkeypatch.setattr(stdio, 'binary_streams', lambda: (io.BytesIO(stdin_bytes), out))
        monkeypatch.setattr(setup_cli, '_handler', lambda _command: fake)
        saved = sys.stdout
        try:
            code = setup_cli.main(argv)
        finally:
            sys.stdout = saved
        return code, out.getvalue(), seen
    return run


def _one_object(raw: bytes) -> dict:
    """The answer must be exactly one JSON object and a newline."""
    text = raw.decode('ascii')
    assert text.endswith('\n') and text.count('\n') == 1
    answer = json.loads(text)
    assert isinstance(answer, dict)
    return answer


class TestInput:

    @pytest.mark.parametrize('stdin_bytes', [
        b'{"a": "' + b'x' * (64 * 1024) + b'"}',
        b'{"a": ',
        b'[1, 2]',
        b'"text"',
        b'\xff\xfe',
        b'',
    ], ids=['over-64-KiB', 'broken-json', 'array', 'string', 'not-utf8', 'empty'])
    def test_bad_input_is_exit_1(self, run_cli, stdin_bytes):
        """FR-WK-1: stdin over 64 KiB, bad JSON or not one object gives exit 1 ``bad_input``."""
        code, out, seen = run_cli(['--setup-apply'], stdin_bytes)
        answer = _one_object(out)
        assert code == 1 and answer['error'] == 'bad_input' and isinstance(answer['message'], str)
        assert seen == []

    def test_an_object_of_exactly_64_KiB_is_read(self, run_cli):
        """FR-WK-1: the limit is at most 64 KiB, so 65,536 bytes are still taken."""
        raw = b'{"a": "x"}'
        raw = raw[:-1] + b' ' * (64 * 1024 - len(raw)) + b'}'
        assert len(raw) == 64 * 1024
        code, out, seen = run_cli(['--setup-cards'], raw)
        assert (code, _one_object(out), seen) == (0, {'ok': True}, [{'a': 'x'}])


class TestArguments:

    @pytest.mark.parametrize('argv', [
        ['--setup-cards', 'extra'], [], ['--setup-other'], ['--setup-apply', '--setup-cards'],
    ], ids=['extra-argument', 'none', 'unknown', 'two-commands'])
    def test_other_arguments_are_exit_2(self, run_cli, argv):
        """FR-WK-1: anything but exactly one known command exits 2 without reading stdin."""
        code, out, seen = run_cli(argv)
        answer = _one_object(out)
        assert (code, answer['error']) == (2, 'usage_error') and seen == []


class TestOutput:

    def test_a_stray_print_goes_to_stderr(self, run_cli, capsys):
        """FR-WK-1: exactly one JSON object on stdout; a ``print`` in a command lands on stderr."""
        def noisy(_request):
            print('stray line from a library')
            return {'cards': []}
        code, out, _seen = run_cli(['--setup-cards'], b'{}', noisy)
        assert (code, _one_object(out)) == (0, {'cards': []})
        assert 'stray line from a library' in capsys.readouterr().err

    def test_an_unexpected_error_is_exit_2_without_details(self, run_cli, caplog):
        """FR-WK-1: an internal error still answers one object (exit 2); the details go to the worker log."""
        def broken(_request):
            raise RuntimeError('secret internal detail')
        with caplog.at_level(logging.ERROR, logger=setup_cli.__name__):
            code, out, _seen = run_cli(['--setup-apply'], b'{}', broken)
        answer = _one_object(out)
        assert (code, answer['error']) == (2, 'internal_error')
        assert 'secret internal detail' not in answer['message']
        assert any(r.exc_info and 'secret internal detail' in str(r.exc_info[1]) for r in caplog.records)

    def test_no_stdout_at_all_is_exit_2(self, monkeypatch):
        """FR-WK-1: with no stdout to answer on (a windowed exe started without pipes) the command exits 2."""
        monkeypatch.setattr(stdio, 'binary_streams', lambda: (io.BytesIO(b'{}'), None))
        assert setup_cli.main(['--setup-cards']) == 2


class TestStreams:
    """The windowed frozen ``run_worker.exe`` may start with ``sys.stdin``/``sys.stdout`` set to ``None`` (FR-WK-1)."""

    def test_missing_std_stream_falls_back_to_the_descriptor(self, monkeypatch):
        opened = []
        monkeypatch.setattr(stdio.os, 'fdopen', lambda fd, mode, closefd: opened.append((fd, mode, closefd)) or fd)
        assert stdio._stream(None, 1, 'wb') == 1
        assert opened == [(1, 'wb', False)]

    def test_unusable_descriptor_gives_none(self, monkeypatch):
        def fail(*_args, **_kwargs):
            raise OSError('bad file descriptor')
        monkeypatch.setattr(stdio.os, 'fdopen', fail)
        assert stdio._stream(None, 0, 'rb') is None

    def test_no_stdin_is_bad_input(self):
        with pytest.raises(stdio.CliError) as caught:
            stdio.read_request(None)
        assert caught.value.code == 'bad_input'


def test_log_goes_only_to_the_worker_log(monkeypatch, tmp_path):
    """FR-WK-1: the commands log to ``<data folder>/logs/worker.log`` only, never to stdout or stderr."""
    from sethlans_worker_agent import config_store
    monkeypatch.setattr(config_store, 'get_data_dir', lambda: tmp_path)
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        stdio.log_to_worker_log()
        [handler] = root.handlers
        logging.getLogger('sethlans_worker_agent.test').info('a line for the worker log')
        handler.flush()
        assert isinstance(handler, logging.FileHandler)
        assert 'a line for the worker log' in (tmp_path / 'logs' / 'worker.log').read_text(encoding='utf-8')
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        logging.captureWarnings(False)
