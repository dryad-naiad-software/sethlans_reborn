# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""#348: the unauthenticated ``/api/health/`` shows a fixed summary of
``last_failure``, never its text (admin names, database hosts, paths)."""
import pytest

from sethlans_manager.setup_mode import failure_summary

SEE_WHY = " Enter the setup token to see why."
USERNAME = "secret-admin-name"
DB_HOST = "db-host.internal.example"
ACCOUNT_PATH = r"C:\Users\osaccount\AppData\Local\Sethlans"

SUMMARIES = [
    (f"First start failed: the account '{USERNAME}' already exists; run Reset setup",
     "The manager's first start failed." + SEE_WHY, "first-start"),
    (f"Database migration failed: connection to server at \"{DB_HOST}\" (10.1.2.3), port 5432 failed: "
     "FATAL: password authentication failed for user \"seth\"",
     "The database could not be prepared." + SEE_WHY, "migration-colon"),
    ("Database migration failed. Error: Command '['manage.py', 'migrate']' returned non-zero exit status 1.",
     "The database could not be prepared." + SEE_WHY, "migration-dot"),
    (f"The data folder cannot be used: {ACCOUNT_PATH} is not owned by you. Run Reset setup to choose another.",
     "The data folder cannot be used." + SEE_WHY, "data-folder"),
    (f"Django setup failed: No module named 'x' in {ACCOUNT_PATH}",
     "The manager could not start." + SEE_WHY, "django-setup"),
    (f"TLS certificate error: [Errno 13] Permission denied: '{ACCOUNT_PATH}\\tls\\cert.pem'",
     "The manager could not start." + SEE_WHY, "tls"),
    ("The manager stopped with exit code 11", "The manager stopped with exit code 11", "exit-code-kept"),
    ("The manager stopped with exit code -9", "The manager stopped with exit code -9", "negative-exit-code-kept"),
    (f"The manager stopped with exit code 11 ({ACCOUNT_PATH})", "The manager did not start." + SEE_WHY,
     "exit-code-with-more-text"),
    ("The manager stopped with exit code ١١", "The manager did not start." + SEE_WHY, "non-ascii-digits"),
    (f"The worker {USERNAME} stopped with exit code 3", "The manager did not start." + SEE_WHY,
     "unknown-program"),
    (f"database error: {DB_HOST} refused the login", "The manager did not start." + SEE_WHY, "unknown-prefix"),
    ("first start failed: lower case", "The manager did not start." + SEE_WHY, "prefix-is-case-sensitive"),
    (f"Setup said First start failed: {USERNAME}", "The manager did not start." + SEE_WHY, "prefix-not-at-start"),
]


@pytest.mark.parametrize("reason, expected", [(r, e) for r, e, _ in SUMMARIES], ids=[i for _, _, i in SUMMARIES])
def test_allowlist_table(reason, expected):
    """#348: each known prefix maps to its fixed phrase; a bare exit code is kept; anything else is generic."""
    assert failure_summary.summarize(reason) == expected


def test_no_reason_file_is_none(settings_dir):
    """#348: without ``last_failure`` health keeps answering ``null``."""
    assert failure_summary.last_failure_summary() is None


def _seed(settings_dir, text):
    (settings_dir / "setup").mkdir(parents=True)
    (settings_dir / "setup" / "last_failure").write_text(text, encoding="utf-8")


@pytest.mark.parametrize("reason", [
    f"First start failed: the admin account '{USERNAME}' could not be created (IntegrityError) in {ACCOUNT_PATH}",
    f"database error: '{USERNAME}'@'{DB_HOST}' denied, see {ACCOUNT_PATH}",
], ids=["known-prefix", "unknown-prefix"])
def test_health_never_shows_the_username_or_path(setup_client, settings_dir, reason):
    """#348: health is unauthenticated, so the seeded username, host and path never reach its body."""
    _seed(settings_dir, reason)
    response = setup_client.get("/api/health/")
    raw = response.content.decode("utf-8")
    for secret in (USERNAME, DB_HOST, "osaccount", "AppData"):
        assert secret not in raw
    assert response.json()["last_failure"].endswith(SEE_WHY)
