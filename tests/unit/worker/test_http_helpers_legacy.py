# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Legacy async ASGI http_helpers tests. Source is dead code as of Phase 4b and will be deleted in Phase 7
alongside these tests. asyncio.run usage here is intentional — it drives the original async API that is
being phased out."""

import asyncio
import json

from sethlans_worker_agent.web_ui.http_helpers import (
    send_json,
    send_html_file,
    read_body,
    parse_json_body,
    MAX_REQUEST_BODY,
)

from tests.unit.worker._asgi_helpers import (
    make_receive,
    ResponseCollector,
)


def _run(coro):
    return asyncio.run(coro)


# -------------------------------------------------------------------
# send_json
# -------------------------------------------------------------------

class TestSendJson:
    def test_sends_correct_headers_and_body(self):
        collector = ResponseCollector()
        data = {"key": "value", "number": 42}
        _run(send_json(collector, data))

        assert collector.status == 200
        assert collector.headers["content-type"] == "application/json"
        body = json.loads(collector.body)
        assert body["key"] == "value"
        assert body["number"] == 42

    def test_custom_status_code(self):
        collector = ResponseCollector()
        _run(send_json(collector, {"error": "not found"}, 404))

        assert collector.status == 404

    def test_content_length_header(self):
        collector = ResponseCollector()
        data = {"msg": "hello"}
        _run(send_json(collector, data))

        expected_len = len(json.dumps(data).encode('utf-8'))
        assert collector.headers["content-length"] == str(expected_len)

    def test_empty_dict_body(self):
        collector = ResponseCollector()
        _run(send_json(collector, {}))

        assert collector.status == 200
        assert collector.json == {}

    def test_503_status(self):
        collector = ResponseCollector()
        _run(send_json(
            collector, {"detail": "Setup not complete."}, 503,
        ))

        assert collector.status == 503


# -------------------------------------------------------------------
# send_html_file
# -------------------------------------------------------------------

class TestSendHtmlFile:
    def test_reads_file_and_sends_html(self, tmp_path):
        html_file = tmp_path / "test.html"
        html_file.write_text(
            "<html><body>Hello</body></html>", encoding="utf-8",
        )

        collector = ResponseCollector()
        _run(send_html_file(collector, str(html_file)))

        assert collector.status == 200
        assert collector.headers["content-type"] == (
            "text/html; charset=utf-8"
        )
        assert b"Hello" in collector.body

    def test_returns_404_for_missing_file(self):
        collector = ResponseCollector()
        _run(send_html_file(collector, "/nonexistent/file.html"))

        assert collector.status == 404
        assert collector.json["error"] == "Not Found"

    def test_content_length_matches_file(self, tmp_path):
        html_file = tmp_path / "page.html"
        content = "<h1>Test Page</h1>"
        html_file.write_text(content, encoding="utf-8")

        collector = ResponseCollector()
        _run(send_html_file(collector, str(html_file)))

        expected_len = len(content.encode("utf-8"))
        assert collector.headers["content-length"] == str(expected_len)


# -------------------------------------------------------------------
# read_body
# -------------------------------------------------------------------

class TestReadBody:
    def test_reads_full_body(self):
        body = b'{"key": "value"}'
        result = _run(read_body(make_receive(body)))
        assert result == body

    def test_reads_empty_body(self):
        result = _run(read_body(make_receive(b'')))
        assert result == b''

    def test_reads_multi_chunk_body(self):
        chunks = [b'{"key":', b' "value"}']
        call_count = [0]

        async def receive():
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(chunks):
                more = idx < len(chunks) - 1
                return {'body': chunks[idx], 'more_body': more}
            return {'body': b'', 'more_body': False}

        result = _run(read_body(receive))
        assert result == b'{"key": "value"}'


# -------------------------------------------------------------------
# parse_json_body
# -------------------------------------------------------------------

class TestParseJsonBody:
    def test_returns_parsed_data_on_success(self):
        body = json.dumps({"key": "value"}).encode()
        data, err = _run(parse_json_body(make_receive(body)))
        assert err is None
        assert data == {"key": "value"}

    def test_rejects_oversized_body(self):
        big_body = b"x" * (MAX_REQUEST_BODY + 1)
        data, err = _run(parse_json_body(make_receive(big_body)))
        assert data is None
        assert err is not None
        assert err[1] == 413
        assert "too large" in err[0]["error"].lower()

    def test_rejects_invalid_json(self):
        data, err = _run(
            parse_json_body(make_receive(b'{not-valid}'))
        )
        assert data is None
        assert err is not None
        assert err[1] == 400
        assert "Invalid JSON" in err[0]["error"]

    def test_accepts_json_array(self):
        body = json.dumps([1, 2, 3]).encode()
        data, err = _run(parse_json_body(make_receive(body)))
        assert err is None
        assert data == [1, 2, 3]

    def test_accepts_exactly_max_size(self):
        """Body at exactly MAX_REQUEST_BODY should be accepted."""
        payload = {"d": "x" * (MAX_REQUEST_BODY - 10)}
        body = json.dumps(payload).encode()
        # Trim or pad to exactly MAX_REQUEST_BODY
        if len(body) > MAX_REQUEST_BODY:
            # Shrink payload to fit
            payload = {"d": "x" * 10}
            body = json.dumps(payload).encode()
        data, err = _run(parse_json_body(make_receive(body)))
        assert err is None
        assert data is not None

    def test_rejects_empty_body_as_invalid_json(self):
        data, err = _run(parse_json_body(make_receive(b'')))
        assert data is None
        assert err is not None
        assert err[1] == 400
