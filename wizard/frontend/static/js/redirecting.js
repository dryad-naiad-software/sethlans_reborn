// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Wait/redirect page (redirecting.html) controller.
//
// FR-W-FE5 contract:
//   - Poll GET /api/wizard/runtime-ready/ every 2s
//   - Show a TLS warning above the spinner so the user knows the
//     impending self-signed cert prompt is expected (HIGH-2)
//   - When the runtime is ready, render the runtime URL as a clickable
//     fallback link inside that same banner so the user can retry
//     navigation manually if the auto-assign() fires before they
//     accepted the cert (HIGH-2)
//   - After 60s of continuous booting, surface the launcher log path
//     as an additional hint (FR-W-FE5)
//   - On a "failed" status, stop polling and show the log path as an
//     error
//   - On three consecutive non-2xx responses, enter post-failsafe mode
//     and surface the cached runtime URL — a single transient 5xx must
//     not strand the user (MEDIUM-1)
//   - Time the elapsed counter against a wall-clock deadline rather
//     than a setInterval decrement, since background tabs throttle
//     setInterval callbacks (MEDIUM-5)

import { createApp } from '/static/vendor/petite-vue.js';
import {
  getSessionToken,
  cacheReadyUrl,
  getCachedReadyUrl,
  isSafeRuntimeUrl,
  wizardFetch,
} from '/static/js/common.js';

const POLL_INTERVAL_MS = 2000;
const TICK_INTERVAL_MS = 1000;
const LOG_HINT_AFTER_SECONDS = 60;

// MEDIUM-1: tolerate up to (FAILURE_TOLERANCE - 1) consecutive non-2xx
// responses before tripping post-failsafe. The strict path for
// fetch() rejection is preserved — that genuinely means the wizard
// process has died. See FR-W-FE5.
const FAILURE_TOLERANCE = 3;

const scope = {
  status: 'booting',
  elapsedSeconds: 0,
  runtimeUrl: null,
  logPath: '',
  cachedUrl: null,
  postFailsafeMode: false,
  _pollHandle: null,
  _tickHandle: null,
  _startedAt: 0,
  _consecutiveFailures: 0,

  // logHintVisible — show the log-path hint when:
  //   (a) booting > 60s (FR-W-FE5 fallback, does NOT replace spinner)
  //   (b) post-failsafe with no cached URL
  //   (c) status === 'failed'
  // The visible alert variant differs by case (logHintAlertClass).
  get logHintVisible() {
    if (this.status === 'failed') return true;
    if (this.postFailsafeMode && !this.cachedUrl) return true;
    return (
      this.status === 'booting' &&
      !this.postFailsafeMode &&
      this.elapsedSeconds >= LOG_HINT_AFTER_SECONDS
    );
  },
  get logHintAlertClass() {
    if (this.status === 'failed') return 'alert alert-danger';
    return 'alert alert-warning';
  },
  get logHintRole() {
    return this.status === 'failed' ? 'alert' : 'status';
  },
  // MEDIUM-4: when status === 'failed' the inner region needs to be
  // assertive so screen readers announce the failure heading + body
  // immediately. The default polite cadence is fine while booting.
  get statusLiveRegionPoliteness() {
    return this.status === 'failed' ? 'assertive' : 'polite';
  },
  get logHintTitle() {
    if (this.status === 'failed') return 'Sethlans failed to start.';
    if (this.postFailsafeMode) return 'The setup wizard is no longer reachable.';
    return 'Sethlans is taking longer than usual to start.';
  },

  _stopTimers() {
    if (this._pollHandle !== null) { window.clearInterval(this._pollHandle); this._pollHandle = null; }
    if (this._tickHandle !== null) { window.clearInterval(this._tickHandle); this._tickHandle = null; }
  },

  _enterPostFailsafe() {
    this.postFailsafeMode = true;
    this.cachedUrl = getCachedReadyUrl();
    this._stopTimers();
  },

  // MEDIUM-1: count consecutive non-2xx hits and only trip post-failsafe
  // once the count hits FAILURE_TOLERANCE. fetch() rejection still
  // trips immediately — see _poll().
  _registerFailure() {
    this._consecutiveFailures += 1;
    if (this._consecutiveFailures >= FAILURE_TOLERANCE) {
      this._enterPostFailsafe();
    }
  },

  async _fetchLauncherLogPath(sessionToken) {
    try {
      const response = await wizardFetch('/api/wizard/launcher-log-path/', {
        sessionToken: sessionToken,
      });
      if (!response.ok) return;
      const body = await response.json();
      if (body && typeof body.path === 'string') this.logPath = body.path;
    } catch (_) { /* non-fatal */ }
  },

  async _poll() {
    const sessionToken = getSessionToken();
    if (!sessionToken) { this._enterPostFailsafe(); return; }
    let response;
    try {
      response = await wizardFetch('/api/wizard/runtime-ready/', {
        sessionToken: sessionToken,
      });
    } catch (_) {
      // Network/TLS failure mid-stream — wizard process likely exited.
      // Trip post-failsafe immediately (no tolerance counter applies).
      this._enterPostFailsafe();
      return;
    }
    if (!response.ok) { this._registerFailure(); return; }
    let body;
    try { body = await response.json(); }
    catch (_) { this._registerFailure(); return; }
    // Reset the consecutive-failure counter on any 2xx with a parsable
    // body so an isolated 5xx does not accumulate forever.
    this._consecutiveFailures = 0;
    const status = body && body.status;
    if (status === 'ready' && typeof body.url === 'string' && body.url) {
      // LOW-4: guard against a late poll firing after we have already
      // entered post-failsafe (the fetch was queued before the timer
      // was cleared).
      if (this.postFailsafeMode) return;
      // LOW-5: validate the runtime URL scheme before honouring it.
      if (!isSafeRuntimeUrl(body.url)) {
        this._registerFailure();
        return;
      }
      this.status = 'ready';
      this.runtimeUrl = body.url;
      // CRITICAL (FE-v2.2-MED-1): write the cache BEFORE navigation so
      // the post-failsafe recovery path can surface the URL when
      // fetch() to the wizard rejects mid-stream.
      cacheReadyUrl(body.url);
      this._stopTimers();
      window.setTimeout(() => { window.location.assign(body.url); }, 0);
      return;
    }
    if (status === 'failed') {
      this.status = 'failed';
      if (typeof body.log_path === 'string' && body.log_path) this.logPath = body.log_path;
      this._stopTimers();
      return;
    }
    this.status = 'booting';
  },

  // Wall-clock based elapsed counter (MEDIUM-5). setInterval is
  // throttled in background tabs; using a stored start time keeps the
  // displayed seconds honest when the user tabs away and back.
  _tick() {
    const elapsed = Math.max(0, Math.floor((Date.now() - this._startedAt) / 1000));
    this.elapsedSeconds = elapsed;
  },

  start() {
    this._startedAt = Date.now();
    const sessionToken = getSessionToken();
    if (sessionToken) this._fetchLauncherLogPath(sessionToken);
    this._tickHandle = window.setInterval(() => this._tick(), TICK_INTERVAL_MS);
    this._pollHandle = window.setInterval(() => this._poll(), POLL_INTERVAL_MS);
    // Kick the first poll immediately so the user does not wait 2s.
    this._poll();
  },
};

document.addEventListener('DOMContentLoaded', () => {
  createApp(scope).mount('#app');
  scope.start();
});
