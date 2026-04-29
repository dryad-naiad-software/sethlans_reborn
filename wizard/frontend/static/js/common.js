// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Shared helpers for the wizard frontend pages.
//
// Centralizes:
//   - sessionStorage access (try/catch wrappers for privacy mode)
//   - one-shot flash-message handoff between pages
//   - session-expiry handling (FR-W-FE3 — clear + redirect with message)
//   - default fetch options (cache: no-store, credentials: omit, the
//     X-Wizard-Session header for authenticated calls)
//
// The wizard intentionally avoids any framework beyond Petite-vue, so
// these helpers are plain ES module exports — no class hierarchy, no
// reactive system. Each page imports what it needs and plugs the
// results into its own Petite-vue scope.

export const SESSION_KEY = 'wizard:sessionToken';
export const FLASH_KEY = 'wizard:flashMessage';
export const LAST_READY_URL_KEY = 'wizard:lastReadyUrl';

// FR-CHK3-RESUME — when a 401 bounces the user back to /token and the
// resume-target endpoint walks them to a page OTHER than where they
// were, the destination renders an info banner reading "Your session
// expired — we've taken you back to the [step label] step where you
// left off." This stash key carries the message between pages.
export const RESUME_BANNER_KEY = 'wizard:resumeBanner';

// FR-CHK3-LABELS — checkpoint-name → human-readable step label. The
// label MUST match the destination page's <h1> exactly (e.g. the
// admin page's heading is "Admin User", NOT "admin-user"). The
// frontend MUST NOT compute the label by string-massaging the route.
export const RESUME_STEP_LABELS = Object.freeze({
  welcome_seen:        'Welcome',
  topology_chosen:     'Topology',
  network_configured:  'Network',
  database_configured: 'Database',
  admin_validated:     'Admin User',
  worker_password_set: 'Worker UI Password',
  ffmpeg_installed:    'FFmpeg',
  verified:            'Verify',
});

// ---------------------------------------------------------------------
// sessionStorage wrappers — every read/write tolerates a SecurityError
// (e.g., the user is browsing in a strict-privacy mode that blocks
// storage). The wizard MUST keep working without storage; it just
// loses the friendly cross-page messages.
// ---------------------------------------------------------------------

export function getSessionToken() {
  try { return window.sessionStorage.getItem(SESSION_KEY); }
  catch (_) { return null; }
}

export function setSessionToken(value) {
  try { window.sessionStorage.setItem(SESSION_KEY, value); }
  catch (_) { /* sessionStorage may be disabled */ }
}

export function clearSessionToken() {
  try { window.sessionStorage.removeItem(SESSION_KEY); }
  catch (_) { /* sessionStorage may be disabled */ }
}

export function setFlash(message) {
  try { window.sessionStorage.setItem(FLASH_KEY, message); }
  catch (_) { /* sessionStorage may be disabled */ }
}

// Read-once: returns the message AND removes it so a refresh on the
// landing page does not re-show a stale "session expired" banner.
export function consumeFlash() {
  let message = null;
  try {
    message = window.sessionStorage.getItem(FLASH_KEY);
    if (message !== null) {
      window.sessionStorage.removeItem(FLASH_KEY);
    }
  } catch (_) {
    return null;
  }
  return message;
}

export function getCachedReadyUrl() {
  try { return window.sessionStorage.getItem(LAST_READY_URL_KEY); }
  catch (_) { return null; }
}

// Validate the URL scheme before caching/honouring it (LOW-5). The
// wizard always serves https, but the runtime hand-off URL might be
// http during local dev. Anything else is a defensive 404.
export function isSafeRuntimeUrl(url) {
  if (typeof url !== 'string' || !url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:';
  } catch (_) {
    return false;
  }
}

export function cacheReadyUrl(url) {
  if (!isSafeRuntimeUrl(url)) return;
  try { window.sessionStorage.setItem(LAST_READY_URL_KEY, url); }
  catch (_) { /* sessionStorage may be disabled */ }
}

// ---------------------------------------------------------------------
// Resume banner — FR-CHK3-RESUME. Same one-shot read pattern as the
// flash message above but a separate key so a stale flash from an
// earlier 401 cannot collide with a resume banner.
// ---------------------------------------------------------------------

export function setResumeBanner(message) {
  try { window.sessionStorage.setItem(RESUME_BANNER_KEY, message); }
  catch (_) { /* sessionStorage may be disabled */ }
}

export function consumeResumeBanner() {
  let message = null;
  try {
    message = window.sessionStorage.getItem(RESUME_BANNER_KEY);
    if (message !== null) {
      window.sessionStorage.removeItem(RESUME_BANNER_KEY);
    }
  } catch (_) {
    return null;
  }
  return message;
}

// ---------------------------------------------------------------------
// expireAndRedirect — used by topology/redirecting pages when a wizard
// API call returns 401/403. Per FR-W-FE3, the session token MUST be
// cleared and the user sent back to the token-entry page with a
// friendly explanation of what happened.
//
// Phase 2 (commit 4ccdfdd) moved welcome.html to ``/`` and the token-
// entry page (``index.html``) to ``/token``. The redirect target MUST
// be ``/token`` so the user lands on the token form, not on the
// welcome page where their next API call would 401 again and loop
// (issue #174).
// ---------------------------------------------------------------------

export function expireAndRedirect(message) {
  clearSessionToken();
  if (typeof message === 'string' && message) {
    setFlash(message);
  }
  window.location.assign('/token');
}

// ---------------------------------------------------------------------
// wizardFetch — thin wrapper around fetch() that always sets the
// no-cache / no-credentials defaults the wizard wants on every request,
// and (when a session token is provided) attaches the X-Wizard-Session
// header automatically. Pass additional headers via opts.headers; they
// merge with — and may override — the defaults.
// ---------------------------------------------------------------------

export function wizardFetch(path, opts) {
  const o = opts || {};
  const headers = Object.assign({}, o.headers || {});
  const sessionToken = (o.sessionToken !== undefined)
    ? o.sessionToken
    : getSessionToken();
  if (sessionToken) {
    headers['X-Wizard-Session'] = sessionToken;
  }
  if (o.body !== undefined && headers['Content-Type'] === undefined) {
    headers['Content-Type'] = 'application/json';
  }
  return fetch(path, {
    method: o.method || 'GET',
    headers: headers,
    body: o.body,
    cache: 'no-store',
    credentials: 'omit',
  });
}
