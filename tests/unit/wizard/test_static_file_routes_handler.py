# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Direct unit tests of the static-file factory.

Exercises the path-traversal logic against a synthetic root rather
than only the real vendored bundle. WSGI page / asset route tests
live in ``test_static_file_routes_pages.py`` and
``test_static_file_routes_assets.py`` so each file stays under the
300-line limit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wizard.sethlans_wizard.handlers.static_files import make_static_handler

from ._static_file_helpers import get_environ, invoke

# The _reset_auth_state autouse fixture is provided by
# tests/unit/wizard/conftest.py.


class TestStaticHandlerDirectly:

    def _make(self, root: Path, prefix: str = "/x/"):
        return make_static_handler(root, prefix)

    def test_make_static_handler_rejects_prefix_without_trailing_slash(
        self, tmp_path,
    ):
        with pytest.raises(ValueError):
            make_static_handler(tmp_path, "/x")

    def test_serves_known_extension(self, tmp_path):
        (tmp_path / "a.css").write_text("body{}", encoding="utf-8")
        handler = self._make(tmp_path)
        status, headers, body = invoke(handler, get_environ("/x/a.css"))
        assert status.startswith("200"), status
        assert headers["Content-Type"].startswith("text/css")
        assert body == b"body{}"

    def test_unknown_extension_404s(self, tmp_path):
        # Even if the file exists, an unknown extension (e.g., .py) must
        # not be served — defense in depth against a stray file.
        (tmp_path / "secret.py").write_text("print(1)", encoding="utf-8")
        handler = self._make(tmp_path)
        status, _, _ = invoke(handler, get_environ("/x/secret.py"))
        assert status.startswith("404"), status

    def test_empty_path_404s(self, tmp_path):
        handler = self._make(tmp_path)
        status, _, _ = invoke(handler, get_environ("/x/"))
        assert status.startswith("404"), status

    def test_path_outside_prefix_404s(self, tmp_path):
        handler = self._make(tmp_path)
        status, _, _ = invoke(handler, get_environ("/y/whatever.css"))
        assert status.startswith("404"), status

    def test_head_returns_no_body(self, tmp_path):
        (tmp_path / "a.css").write_text("body{}", encoding="utf-8")
        handler = self._make(tmp_path)
        env = get_environ("/x/a.css")
        env["REQUEST_METHOD"] = "HEAD"
        status, headers, body = invoke(handler, env)
        assert status.startswith("200")
        assert headers["Content-Length"] == "6"
        assert body == b""

    def test_traversal_via_resolve_blocked(self, tmp_path):
        # Create a file outside the allowed root, then try to escape to
        # it via "..".
        outside = tmp_path.parent / "secret-outside.css"
        outside.write_text("nope", encoding="utf-8")
        try:
            handler = self._make(tmp_path)
            status, _, _ = invoke(
                handler, get_environ("/x/../secret-outside.css"),
            )
            assert status.startswith("404"), status
        finally:
            outside.unlink(missing_ok=True)
