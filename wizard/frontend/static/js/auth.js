// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Token-entry page (index.html) controller.
//
// Implements FR-W-FE3 (token paste + POST /api/wizard/auth/, 401/429
// handling, lockout) and FR-CHK3-RESUME (post-auth resume).
//
// On a successful re-auth POST, this page MUST NOT navigate to / (the
// welcome page) by default. Instead it asks
// `GET /api/wizard/resume-target/` for the route of the first
// incomplete checkpoint and navigates there. If the resume route is
// anything other than `/` (the welcome page) we stash a friendly
// banner explaining the reentry — read once on the destination page.

import { createApp } from '/static/vendor/petite-vue.js';
import {
  setSessionToken,
  consumeFlash,
  setResumeBanner,
  RESUME_STEP_LABELS,
  wizardFetch,
} from '/static/js/common.js';

const LOCKOUT_SECS = 60;

// Reverse map: route → checkpoint name. Used to look up the friendly
// label for the resume banner without re-fetching the checkpoint list.
const ROUTE_TO_CHECKPOINT = {
  '/': 'welcome_seen',
  '/topology': 'topology_chosen',
  '/network': 'network_configured',
  '/database': 'database_configured',
  '/admin-user': 'admin_validated',
  '/worker-password': 'worker_password_set',
  '/ffmpeg': 'ffmpeg_installed',
  '/verify': 'verified',
};

const scope = {
  token: '',
  submitting: false,
  error: '',
  lockoutSeconds: 0,
  _lockoutTimer: null,
  _lockoutDeadline: 0,

  get canSubmit() {
    return this.token.trim().length > 0;
  },

  async submit() {
    if (this.submitting || this.lockoutSeconds > 0) {
      return;
    }
    const trimmed = this.token.trim();
    if (!trimmed) {
      this.error = 'Please enter the setup token.';
      return;
    }
    this.error = '';
    this.submitting = true;
    let response;
    try {
      response = await wizardFetch('/api/wizard/auth/', {
        method: 'POST',
        body: JSON.stringify({ token: trimmed }),
        sessionToken: null,
      });
    } catch (err) {
      this.submitting = false;
      this.error = 'Network error. Check the launcher logs and try again.';
      return;
    }

    if (response.status === 200) {
      let payload;
      try {
        payload = await response.json();
      } catch (_) {
        this.submitting = false;
        this.error = 'Unexpected response from the wizard. Try again.';
        return;
      }
      const sessionToken = payload && payload.session_token;
      if (typeof sessionToken !== 'string' || !sessionToken) {
        this.submitting = false;
        this.error = 'Wizard did not issue a session token. Try again.';
        return;
      }
      setSessionToken(sessionToken);
      this.token = '';
      const next = await this._resolveResumeRoute(sessionToken);
      window.history.replaceState({}, '', next);
      window.location.assign(next);
      return;
    }

    if (response.status === 403) {
      this.submitting = false;
      this.error = 'Invalid setup token. Check the launcher console and try again.';
      return;
    }

    if (response.status === 429) {
      this.submitting = false;
      this.error = 'Too many attempts — please wait 60 seconds and try again.';
      this._startLockout(LOCKOUT_SECS);
      return;
    }

    this.submitting = false;
    this.error = 'Something went wrong. Check the launcher logs and try again.';
  },

  // FR-CHK3-RESUME — query the resume-target endpoint for the first
  // incomplete checkpoint. On any failure, fall back to /.
  async _resolveResumeRoute(sessionToken) {
    let r;
    try {
      r = await wizardFetch('/api/wizard/resume-target/', {
        method: 'GET',
        sessionToken: sessionToken,
      });
    } catch (_) {
      return '/';
    }
    if (!r.ok) return '/';
    let body;
    try { body = await r.json(); }
    catch (_) { return '/'; }
    const route = body && typeof body.route === 'string' ? body.route : '/';
    if (route !== '/') {
      const checkpoint = ROUTE_TO_CHECKPOINT[route];
      const label = checkpoint ? RESUME_STEP_LABELS[checkpoint] : null;
      if (label) {
        setResumeBanner(
          'Your session expired — we’ve taken you back to the '
          + label + ' step where you left off.',
        );
      }
    }
    return route;
  },

  _startLockout(seconds) {
    this._lockoutDeadline = Date.now() + seconds * 1000;
    this.lockoutSeconds = seconds;
    if (this._lockoutTimer) {
      clearInterval(this._lockoutTimer);
    }
    this._lockoutTimer = setInterval(() => {
      const remaining = Math.max(
        0,
        Math.ceil((this._lockoutDeadline - Date.now()) / 1000),
      );
      this.lockoutSeconds = remaining;
      if (remaining <= 0) {
        clearInterval(this._lockoutTimer);
        this._lockoutTimer = null;
      }
    }, 1000);
  },
};

document.addEventListener('DOMContentLoaded', () => {
  const flash = consumeFlash();
  if (flash) {
    scope.error = flash;
  }
  createApp(scope).mount('#app');
  const input = document.getElementById('setup-token');
  if (input) {
    input.focus();
  }
});
