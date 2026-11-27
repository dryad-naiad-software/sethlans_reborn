# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``sethlans_manager.admin_fields``: the first admin's
username and email rules, shared by setup mode and the first start
(FR-WZ-5, FR-FS-1, FR-FS-2). Each rule is tested here once; the callers'
tests only check that they use it.
"""
import pytest

from sethlans_manager import admin_fields

MAX_NAME = admin_fields.MAX_USERNAME_LENGTH
DOMAIN_189 = "b" * 63 + "." + "c" * 63 + "." + "d" * 61
EMAIL_254 = "a" * 64 + "@" + DOMAIN_189


@pytest.mark.parametrize("username, problem", [
    pytest.param("", "the username is empty", id="empty"),
    pytest.param("a" * MAX_NAME, None, id="150-chars"),
    pytest.param("a" * (MAX_NAME + 1), "longer than 150 characters", id="151-chars"),
    pytest.param("farm admin", "Enter a valid username", id="space"),
])
def test_username_problems(username, problem):
    """FR-FS-1, FR-FS-2, FR-WZ-5: a username Django accepts (letters, digits, @.+-_, at most 150) has no problems."""
    problems = admin_fields.username_problems(username)

    if problem is None:
        assert problems == []
    else:
        assert len(problems) == 1 and problem in problems[0]


@pytest.mark.parametrize("raw, username, problem", [
    pytest.param("  boss\t", "boss", None, id="stripped"),
    pytest.param("ｂoss", "boss", None, id="fullwidth-letter-nfkc"),
    pytest.param("a" * (MAX_NAME - 2) + "ﬃ", "a" * (MAX_NAME - 2) + "ffi", "longer than 150 characters",
                 id="length-checked-after-nfkc"),
    pytest.param("   ", "", "the username is empty", id="blank"),
])
def test_clean_username(raw, username, problem):
    """FR-FS-1, FR-WZ-5 (review M7): stripped and NFKC-normalized like Django's normalize_username, then checked,
    so the name setup writes is the name the first start creates."""
    cleaned, problems = admin_fields.clean_username(raw)

    assert cleaned == username
    if problem is None:
        assert problems == []
    else:
        assert len(problems) == 1 and problem in problems[0]


@pytest.mark.parametrize("email, problems", [
    pytest.param("", [], id="empty-allowed"),
    pytest.param(EMAIL_254, [], id="254-chars"),
    pytest.param(EMAIL_254 + "d", ["SETHLANS_ADMIN_EMAIL is longer than 254 characters"], id="255-chars"),
    pytest.param("not-an-email", ["SETHLANS_ADMIN_EMAIL is not a valid email address"], id="invalid"),
])
def test_email_problems(email, problems):
    """FR-FS-1, FR-FS-2: an email may be empty; otherwise at most 254 characters (User.email) that
    validate_email accepts, the problem named by the caller's label."""
    assert admin_fields.email_problems(email, "SETHLANS_ADMIN_EMAIL") == problems
