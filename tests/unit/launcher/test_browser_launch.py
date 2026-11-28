# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher/browser_launch.py`` (FR-13 / FR-14, FR-SEC-1)."""

from __future__ import annotations

from launcher import browser_launch
from launcher.browser_launch import open_browser, print_setup_banner


# ------------------------------------------------------------------
# open_browser — URL never carries ?token=
# ------------------------------------------------------------------

class TestOpenBrowserUrlNoQueryString:

    def test_url_has_no_query_string(self, mocker):
        spy = mocker.patch.object(browser_launch.webbrowser, "open")
        # Force interactive path.
        mocker.patch.object(
            browser_launch, "is_headless", return_value=False,
        )
        open_browser(
            port=8080, no_browser=False, print_url=False,
            path="/setup/", setup_token="TOKEN_VALUE",
        )
        spy.assert_called_once()
        url = spy.call_args.args[0]
        assert url == "https://localhost:8080/setup/"
        assert "?token=" not in url
        assert "TOKEN_VALUE" not in url

    def test_setup_token_kwarg_is_ignored(self, mocker):
        spy = mocker.patch.object(browser_launch.webbrowser, "open")
        mocker.patch.object(
            browser_launch, "is_headless", return_value=False,
        )
        # Pass token — the url must still not include it.
        open_browser(
            8080, False, False, "/setup/",
            setup_token="aaaabbbbcccc",
        )
        url = spy.call_args.args[0]
        assert "aaaabbbbcccc" not in url

    def test_no_browser_skips_open(self, mocker):
        spy = mocker.patch.object(browser_launch.webbrowser, "open")
        mocker.patch.object(
            browser_launch, "is_headless", return_value=False,
        )
        open_browser(8080, no_browser=True, print_url=False, path="/")
        spy.assert_not_called()

    def test_headless_skips_open(self, mocker):
        spy = mocker.patch.object(browser_launch.webbrowser, "open")
        mocker.patch.object(
            browser_launch, "is_headless", return_value=True,
        )
        open_browser(8080, False, False, "/")
        spy.assert_not_called()


# ------------------------------------------------------------------
# print_setup_banner — URL, token and the served certificate's SHA-256
# ------------------------------------------------------------------

def test_banner_prints_url_token_and_fingerprint_of_the_manager_cert(mocker, capsys, tmp_path):
    """Spec common start step 3, SR nit 4 and nit 10, issue #349: the banner
    shows the URL, the token and the SHA-256 of the manager's own certificate
    (the one Caddy serves, ``caddy_wiring.manager_tls_paths``) over its DER
    bytes, the value ``get_cert_fingerprint`` gives and workers pin; nothing
    copies the token."""
    from cryptography import x509

    from shared.cert_utils import get_cert_fingerprint
    from tests.unit.worker._tls_loopback import _self_signed

    cert, key, _der_sha = _self_signed(tmp_path)
    mocker.patch("launcher.caddy_wiring.manager_tls_paths", return_value=(cert, key))
    expected = get_cert_fingerprint(x509.load_pem_x509_certificate(cert.read_bytes()))

    result = print_setup_banner(port=8081, wizard_path="/setup/", setup_token="TOKENVALUE")

    out, err = capsys.readouterr()
    assert result is None
    assert "https://localhost:8081/setup/" in out
    assert "Token:  TOKENVALUE" in out
    assert "clipboard" not in out.lower()
    shown = err.split("sha256:", 1)[1].split()[0]
    assert shown.replace(":", "") == expected
