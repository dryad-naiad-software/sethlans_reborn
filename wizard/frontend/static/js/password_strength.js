// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Client-side password strength helper for the admin-user wizard step.
//
// Mirrors the rules that Django's configured AUTH_PASSWORD_VALIDATORS
// will enforce server-side (see manager/sethlans_manager/settings.py):
//   - MinimumLengthValidator (default 8) → "at least 8 characters"
//   - NumericPasswordValidator           → "not entirely digits"
//   - UserAttributeSimilarityValidator   → "not similar to username/email"
//
// CommonPasswordValidator is NOT mirrored on the client — Django ships
// a ~20k-entry common-password list that's too heavy to bundle. The
// server still enforces it; failing client-side strength != server pass.
//
// The similarity check is a coarse approximation of Django's
// SequenceMatcher-based check: we treat a password as "similar" if its
// lowercased form contains the username/email-local lowercased and >=
// 3 chars. Good enough to catch the obvious "username = password" case
// without re-implementing SequenceMatcher in the browser.

const MIN_LENGTH = 8;

function _hasMinLength(pw) {
  return pw.length >= MIN_LENGTH;
}

function _isNotAllNumeric(pw) {
  if (!pw) return false;
  return !/^\d+$/.test(pw);
}

function _emailLocal(email) {
  if (typeof email !== 'string' || !email) return '';
  const at = email.indexOf('@');
  return at > 0 ? email.slice(0, at) : email;
}

function _isNotSimilarTo(pw, attrs) {
  if (!pw) return false;
  const lowered = pw.toLowerCase();
  for (const raw of attrs) {
    if (typeof raw !== 'string') continue;
    const v = raw.trim().toLowerCase();
    if (v.length < 3) continue;
    if (lowered.includes(v) || v.includes(lowered)) return false;
  }
  return true;
}

// Return {score, max, checks: [{id, label, passed}]}.
// `score` = checks passed; `max` = total checks. `attrs` is the list of
// strings the password must NOT resemble (typically [username, email]).
// When `attrs` is empty/missing the similarity check is OMITTED rather
// than auto-passed — pages without user-attribute context (e.g. the
// worker password page) shouldn't render a check that's impossible to
// fail.
export function evaluatePassword(password, attrs) {
  const pw = typeof password === 'string' ? password : '';
  const userAttrs = [];
  if (Array.isArray(attrs)) {
    for (const a of attrs) {
      if (typeof a === 'string' && a) userAttrs.push(a);
    }
  }
  // Email's local part trips the similarity check too.
  for (const a of [...userAttrs]) {
    const local = _emailLocal(a);
    if (local && local !== a) userAttrs.push(local);
  }
  const checks = [
    {
      id: 'length',
      label: `At least ${MIN_LENGTH} characters`,
      passed: _hasMinLength(pw),
    },
    {
      id: 'not_numeric',
      label: 'Not entirely digits',
      passed: _isNotAllNumeric(pw),
    },
  ];
  if (userAttrs.length > 0) {
    checks.push({
      id: 'not_similar',
      label: 'Not similar to your username or email',
      passed: _isNotSimilarTo(pw, userAttrs),
    });
  }
  const score = checks.reduce((n, c) => n + (c.passed ? 1 : 0), 0);
  return { score, max: checks.length, checks };
}

// Map score → CSS class name. The bar is hidden via empty class when the
// password is empty so a fresh form doesn't show "weak" before the user
// has typed anything.
export function strengthClass(score, max) {
  if (max <= 0 || score <= 0) return '';
  if (score >= max) return 'strength-strong';
  if (score >= Math.ceil(max / 2)) return 'strength-medium';
  return 'strength-weak';
}

export const STRENGTH_MIN_LENGTH = MIN_LENGTH;
