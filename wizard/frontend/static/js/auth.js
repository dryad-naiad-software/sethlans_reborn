// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Token-entry page (index.html) controller.
//
// Implements FR-W-FE3 (token paste + POST /api/wizard/auth/, 401/429
// handling, lockout). Reads the wizard:flashMessage flash on mount so
// users redirected here from a 401 on a downstream page see the
// "Your session expired" explanation immediately (HIGH-3).
//
// Lockout timing uses a wall-clock target (Date.now() + N*1000) instead
// of a setInterval decrement counter; background tabs throttle
// setInterval to ~1Hz, so a naive decrement can drift by tens of
// seconds (MEDIUM-5).

import { createApp } from '/static/vendor/petite-vue.js';
import {
  setSessionToken,
  consumeFlash,
  wizardFetch,
} from '/static/js/common.js';

const LOCKOUT_SECS = 60;

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
        sessionToken: null, // explicit: pre-auth, no session header
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
      // Replace history so the browser Back button does not return the
      // user to the token-entry page (where re-submitting the same
      // token consumes a rate-limit attempt) — MEDIUM-2.
      window.history.replaceState({}, '', '/topology');
      window.location.assign('/topology');
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

  _startLockout(seconds) {
    // Wall-clock based: store the absolute deadline and recompute
    // remaining seconds on every tick. setInterval is throttled in
    // background tabs (Chromium clamps to ~1s but skips ticks under
    // load), so a naive `lockoutSeconds -= 1` can over-report the
    // remaining wait. MEDIUM-5.
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
  // HIGH-3: surface the flash message that topology.html
  // (or another page) wrote when it bounced the user back here.
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
