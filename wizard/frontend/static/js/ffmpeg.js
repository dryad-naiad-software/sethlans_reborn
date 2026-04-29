// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// FFmpeg page (ffmpeg.html) controller (FR-M2-7).
//
// On mount: POST /api/wizard/ffmpeg/start/ (idempotent under the
// single-task invariant), then poll /progress/<task_id>/ at 1 Hz until
// status is `complete` or `failed`. Failure copy + retry-allowed table
// live in ./ffmpeg_failures.js. Form-state stash is cleared on
// `complete` per FR-CHK3-FORM-STATE.

import { createApp, reactive } from '/static/vendor/petite-vue.js';
import {
  consumeResumeBanner,
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';
import { clearAllFormState } from '/static/js/form_state.js';
import {
  STEP_FFMPEG,
  backRouteFor,
  buildStepperModel,
  fetchChromeContext,
} from '/static/js/wizard_chrome.js';
import {
  FAILURE_HEADLINES,
  FAILURE_BODIES,
  RETRY_ALLOWED,
} from '/static/js/ffmpeg_failures.js';

const POLL_INTERVAL_MS = 1000;
// Issue #182 — cap consecutive non-2xx polls so a vanished task (404
// unknown_task, transient 5xx) surfaces a Retry button instead of an
// indefinite spinner. 3 polls is ~3s — absorbs jitter, surfaces outages.
const MAX_CONSECUTIVE_POLL_FAILURES = 3;

const scope = reactive({
  status: 'starting',
  percent: 0,
  taskId: '',
  category: '',
  errorDetail: '',
  resumeBanner: '',
  _pollHandle: null,
  _started: false,       // Issue #182 — guard against double start().
  _pollFailures: 0,      // Issue #182 — see MAX_CONSECUTIVE_POLL_FAILURES.
  topology: null,
  stepper: buildStepperModel(
    null, STEP_FFMPEG,
    [
      'topology_chosen', 'network_configured',
      'database_configured', 'admin_validated',
    ],
  ),

  goBack() {
    const route = backRouteFor(STEP_FFMPEG, this.topology);
    if (route) {
      this._stopPolling();
      window.location.assign(route);
    }
  },

  get busy() {
    return this.status === 'downloading'
      || this.status === 'extracting'
      || this.status === 'verifying'
      || this.status === 'starting';
  },

  get failureHeadline() {
    return FAILURE_HEADLINES[this.category] || FAILURE_HEADLINES.generic;
  },
  get failureBody() {
    return FAILURE_BODIES[this.category] || FAILURE_BODIES.generic;
  },
  get retryAllowed() {
    return RETRY_ALLOWED.has(this.category || 'generic');
  },

  async start() {
    if (this._started) return;  // Issue #182 — idempotent.
    this._started = true;
    this._pollFailures = 0;
    let response;
    try {
      response = await wizardFetch('/api/wizard/ffmpeg/start/', { method: 'POST' });
    } catch (_) {
      this._failGeneric('Network error.');
      return;
    }
    if (response.status === 401 || response.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (!response.ok) {
      this._failGeneric('Could not start FFmpeg download.');
      return;
    }
    let payload;
    try { payload = await response.json(); }
    catch (_) { this._failGeneric('Bad response.'); return; }
    if (!payload || typeof payload.task_id !== 'string') {
      this._failGeneric('Bad response.');
      return;
    }
    this.taskId = payload.task_id;
    this.status = (payload.status === 'in_progress') ? 'downloading' : 'starting';
    this._beginPolling();
  },

  _beginPolling() {
    if (this._pollHandle !== null) return;
    this._pollHandle = window.setInterval(() => this._poll(), POLL_INTERVAL_MS);
    this._poll();
  },

  _stopPolling() {
    if (this._pollHandle !== null) {
      window.clearInterval(this._pollHandle);
      this._pollHandle = null;
    }
  },

  async _poll() {
    if (!this.taskId) return;
    let response;
    try {
      response = await wizardFetch(
        '/api/wizard/ffmpeg/progress/' + encodeURIComponent(this.taskId) + '/',
      );
    } catch (_) {
      this._recordPollFailure();
      return;
    }
    if (response.status === 401 || response.status === 403) {
      this._stopPolling();
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (!response.ok) {
      this._recordPollFailure();
      return;
    }
    let body;
    try { body = await response.json(); }
    catch (_) { this._recordPollFailure(); return; }
    if (!body || typeof body.status !== 'string') {
      this._recordPollFailure();
      return;
    }
    this._pollFailures = 0;  // 2xx + parseable body resets the streak.
    this.status = body.status;
    if (typeof body.percent === 'number') this.percent = body.percent;
    if (typeof body.category === 'string') this.category = body.category;
    if (typeof body.error === 'string') this.errorDetail = body.error;
    if (this.status === 'complete') {
      this.percent = 100;
      this._stopPolling();
      clearAllFormState();  // FR-CHK3-FORM-STATE.
    } else if (this.status === 'failed') {
      this._stopPolling();
    }
  },

  retry() {
    this.status = 'starting';
    this.percent = 0;
    this.category = '';
    this.errorDetail = '';
    this.taskId = '';
    this._started = false;  // Clear #182 guard so start() re-fires.
    this._pollFailures = 0;
    this.start();
  },

  _recordPollFailure() {
    this._pollFailures += 1;
    if (this._pollFailures >= MAX_CONSECUTIVE_POLL_FAILURES) {
      this._failGeneric('Lost contact with the FFmpeg installer task. Retry to start a fresh download.');
    }
  },

  _failGeneric(detail) {
    this.status = 'failed';
    this.category = 'generic';
    this.errorDetail = detail || '';
    this._stopPolling();
  },

  next() {
    if (this.status !== 'complete') return;
    window.location.assign('/verify');
  },
});

// Issue #182 — bfcache restore fires pageshow (event.persisted=true)
// but NOT DOMContentLoaded; hooking both keeps revisits unstuck.
let _mounted = false;
function _mountOnce() {
  if (_mounted) return;
  _mounted = true;
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  createApp(scope).mount('#app');
}

function _kickoffStart() {
  scope.start();
  // Topology is unknown sync — paint manager stepper, widen async.
  (async () => {
    const ctx = await fetchChromeContext();
    scope.topology = ctx.topology;
    const completed = [
      'topology_chosen', 'network_configured',
      'database_configured', 'admin_validated',
    ];
    if (ctx.topology === 'manager_worker') completed.push('worker_password_set');
    scope.stepper = buildStepperModel(ctx.topology, STEP_FFMPEG, completed);
  })();
}

function _resetForRevisit() {
  // Bfcache preserves the reactive scope; clear prior terminal state
  // so the fresh /start/ does not flash stale 'complete' first.
  scope._started = false;
  scope._pollFailures = 0;
  scope.taskId = '';
  scope.status = 'starting';
  scope.percent = 0;
  scope.category = '';
  scope.errorDetail = '';
}

document.addEventListener('DOMContentLoaded', () => {
  _mountOnce();
  _kickoffStart();
});

window.addEventListener('pageshow', (event) => {
  if (!event.persisted) return;
  _resetForRevisit();
  _mountOnce();
  _kickoffStart();
});
