# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The final status report fits the manager's request body limit (#260).

``last_output`` keeps the longest marked tail of the render output whose
JSON form fits ``LAST_OUTPUT_MAX_BYTES``, starting at a line start when it
can; ``error_message`` keeps its marked head within
``ERROR_MESSAGE_MAX_BYTES``. Sizes count the JSON escapes that non-ASCII
and control characters turn into. Short text is sent unchanged.
"""
import json

import requests

from sethlans_worker_agent import job_processor, job_report, report_limits
from sethlans_worker_agent.report_limits import (
    ERROR_MESSAGE_MAX_BYTES, LAST_OUTPUT_MAX_BYTES, OMITTED_HEAD_MARKER, OMITTED_TAIL_MARKER,
    bounded_head, bounded_tail, json_size,
)
from tests.unit.worker.executors._fakes import make_result

MANAGER_BODY_LIMIT = 128 * 1024  # SMALL_BODY_LIMIT on the manager
# Non-ASCII (2-, 3- and 4-byte UTF-8), ESC/BEL/NUL, a surrogateescape byte, quotes and backslashes:
# all become JSON escapes, several bytes each.
_NOISY = 'é漢😀 \x1b[0m\x07\x00 \udcff "q" \\ '


def _render_output(size=1024 * 1024):
    lines, total, i = [], 0, 0
    while total < size:
        line = f"Fra:1 Mem:812.4M | Time:00:{i % 60:02d} | Sample {i}/4096 {_NOISY}\n"
        lines.append(line)
        total += len(line.encode('utf-8', 'surrogateescape'))
        i += 1
    lines.append("Saved: 'frame_0001.png'\n")
    return ''.join(lines)


def _report(mocker, result):
    mocker.patch('sethlans_worker_agent.api_handler.upload_render_output', return_value=True)
    patch = mocker.patch('sethlans_worker_agent.api_handler.update_job_status', return_value=True)
    job_report.report_result({'id': 4, 'name': 'Frame'}, result, job_processor)
    return patch.call_args.args[1]


def _sent_body(payload):
    """The bytes requests sends for ``json=payload``."""
    return requests.Request('PATCH', 'https://manager.invalid/api/jobs/4/', json=payload).prepare().body


class TestStatusReportSize:

    def test_megabyte_output_and_error_fit_the_manager_limit(self, mocker):
        stdout = _render_output()
        assert json_size(stdout) > 5 * MANAGER_BODY_LIMIT  # the escapes make it much larger than 1 MB

        payload = _report(mocker, make_result(stdout=stdout, error_message=_NOISY * 50_000, success=False))

        assert len(json.dumps(payload)) < MANAGER_BODY_LIMIT
        assert len(_sent_body(payload)) < MANAGER_BODY_LIMIT
        assert json_size(payload['last_output']) <= LAST_OUTPUT_MAX_BYTES
        assert json_size(payload['error_message']) <= ERROR_MESSAGE_MAX_BYTES

    def test_last_output_is_the_end_of_the_output_from_a_line_start(self, mocker):
        stdout = _render_output()

        sent = _report(mocker, make_result(stdout=stdout))['last_output']

        assert sent.startswith(OMITTED_HEAD_MARKER)
        tail = sent[len(OMITTED_HEAD_MARKER):]
        assert tail.endswith("Saved: 'frame_0001.png'\n")
        assert stdout.endswith(tail)
        assert stdout[-len(tail) - 1] == '\n'  # whole lines only
        # Longest tail: one more line would not fit.
        line_before = stdout[:-len(tail)].splitlines(keepends=True)[-1]
        assert json_size(OMITTED_HEAD_MARKER + line_before + tail) > LAST_OUTPUT_MAX_BYTES

    def test_render_time_is_still_parsed_from_the_full_output(self, mocker):
        stdout = "Fra:1 Time:00:01.00\n" + _render_output(300_000) + " Time: 01:02.50 (Saving: 00:00.10)\n"
        mocker.patch.object(job_report, 'parse_render_time', return_value=62.5)

        payload = _report(mocker, make_result(stdout=stdout, render_time_seconds=None))

        job_report.parse_render_time.assert_called_once_with(stdout)
        assert payload['render_time_seconds'] == 62.5

    def test_short_output_and_error_are_unchanged(self, mocker):
        stdout = "Blender 5.2\nFra:1 Sample 1/16 é漢\x1b\nSaved: 'frame_0001.png'\n"

        payload = _report(mocker, make_result(stdout=stdout, error_message='boom é', success=False))

        assert payload['last_output'] == stdout
        assert payload['error_message'] == 'boom é'


class TestBoundedTail:

    def test_text_at_the_bound_is_unchanged_and_one_byte_more_is_cut(self):
        text = 'a' * (LAST_OUTPUT_MAX_BYTES - 2)  # plus the two quotes
        assert bounded_tail(text) is text

        cut = bounded_tail(text + 'b')
        assert cut.startswith(OMITTED_HEAD_MARKER)
        assert json_size(cut) <= LAST_OUTPUT_MAX_BYTES

    def test_a_single_long_line_is_cut_mid_line_to_the_longest_tail(self):
        text = '漢' * 100_000  # 6 bytes each in JSON, no line break

        tail = bounded_tail(text)[len(OMITTED_HEAD_MARKER):]

        assert text.endswith(tail)
        assert json_size(OMITTED_HEAD_MARKER + tail) <= LAST_OUTPUT_MAX_BYTES
        assert json_size(OMITTED_HEAD_MARKER + '漢' + tail) > LAST_OUTPUT_MAX_BYTES

    def test_only_a_final_line_break_keeps_the_partial_line(self):
        text = 'x' * 200_000 + '\n'

        tail = bounded_tail(text)[len(OMITTED_HEAD_MARKER):]

        assert tail.endswith('x\n') and len(tail) > 1

    def test_non_strings_pass_through(self):
        assert bounded_tail(None) is None
        assert bounded_head(None) is None

    def test_custom_bound(self):
        text = 'a first line long enough to be dropped\nline two\nline three\n'

        out = bounded_tail(text, max_bytes=json_size(OMITTED_HEAD_MARKER) + 14)

        assert out == OMITTED_HEAD_MARKER + 'line three\n'


class TestBoundedHead:

    def test_long_error_keeps_its_start(self):
        message = 'Blender exited with code 1. Details: ' + '😀' * 10_000

        sent = bounded_head(message)

        assert sent.endswith(OMITTED_TAIL_MARKER)
        head = sent[:-len(OMITTED_TAIL_MARKER)]
        assert message.startswith(head) and head.startswith('Blender exited with code 1.')
        assert json_size(sent) <= ERROR_MESSAGE_MAX_BYTES
        assert json_size(message[:len(head) + 1] + OMITTED_TAIL_MARKER) > ERROR_MESSAGE_MAX_BYTES

    def test_constants(self):
        """Both fields together stay far below the manager's 128 KiB limit."""
        assert report_limits.LAST_OUTPUT_MAX_BYTES + report_limits.ERROR_MESSAGE_MAX_BYTES <= 96 * 1024
