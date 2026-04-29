// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// FFmpeg page (ffmpeg.html) controller (FR-M2-7).
//
// On mount: POST /api/wizard/ffmpeg/start/. Idempotent under the
// single-task invariant — start may return an existing task_id when
// the user revisits the page mid-download.
//
// Polls /api/wizard/ffmpeg/progress/<task_id>/ at 1 Hz until status
// is `complete` or `failed`. Failure category drives the headline /
// retry affordance (FR-M2-7 taxonomy):
//   download_failed   — retry allowed.
//   sha_mismatch      — retry NOT allowed (re-pin required).
//   version_mismatch  — retry NOT allowed (re-pin required).
//   extraction_failed — retry allowed.
//   network_error     — retry allowed.
//
// Form-state stash is cleared on `complete` per FR-CHK3-FORM-STATE.

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

const POLL_INTERVAL_MS = 1000;

const FAILURE_HEADLINES = {
  download_failed: 'Could not download FFmpeg.',
  sha_mismatch: 'FFmpeg download verification failed.',
  version_mismatch: 'FFmpeg installed but reports an unexpected version.',
  extraction_failed: 'FFmpeg archive could not be extracted.',
  network_error: 'Network error during download.',
  generic: 'FFmpeg installation failed.',
};

const FAILURE_BODIES = {
  download_failed: 'Check your network connection and try again.',
  sha_mismatch: 'The file does not match the expected checksum. Setup cannot proceed safely.',
  version_mismatch: 'Setup cannot proceed without the expected FFmpeg version.',
  extraction_failed: 'The download finished but the archive contents were unsafe or corrupt.',
  network_error: 'A network error occurred during the download.',
  generic: 'Check the launcher logs for the underlying error.',
};

const RETRY_ALLOWED = new Set(['download_failed', 'extraction_failed', 'network_error', 'generic']);

const scope = reactive({
  status: 'starting',
  percent: 0,
  taskId: '',
  category: '',
  errorDetail: '',
  resumeBanner: '',
  _pollHandle: null,
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
    } catch (_) { return; /* transient — keep polling */ }
    if (response.status === 401 || response.status === 403) {
      this._stopPolling();
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (!response.ok) return;
    let body;
    try { body = await response.json(); }
    catch (_) { return; }
    if (!body || typeof body.status !== 'string') return;
    this.status = body.status;
    if (typeof body.percent === 'number') this.percent = body.percent;
    if (typeof body.category === 'string') this.category = body.category;
    if (typeof body.error === 'string') this.errorDetail = body.error;
    if (this.status === 'complete') {
      this.percent = 100;
      this._stopPolling();
      // FR-CHK3-FORM-STATE: clear form state past the irreversible
      // installer boundary.
      clearAllFormState();
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
    this.start();
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

document.addEventListener('DOMContentLoaded', () => {
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  createApp(scope).mount('#app');
  scope.start();
  // The set of completed checkpoints depends on topology — manager
  // skips the worker-password step entirely. We can't know the
  // topology synchronously, so apply a manager-flavoured stepper now
  // and let `applyChromeContext` widen it once /resume-target reports
  // the actual topology.
  (async () => {
    const ctx = await fetchChromeContext();
    scope.topology = ctx.topology;
    const completed = [
      'topology_chosen', 'network_configured',
      'database_configured', 'admin_validated',
    ];
    if (ctx.topology === 'manager_worker') {
      completed.push('worker_password_set');
    }
    scope.stepper = buildStepperModel(ctx.topology, STEP_FFMPEG, completed);
  })();
});
