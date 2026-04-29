// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Verify page (verify.html) controller (FR-M2-8 / FR-M2-8a).
//
// POSTs /api/wizard/verify/ on mount and renders the resulting
// checklist with green-check / red-X icons. The handler caches
// results for 60s; a "Re-run checks" button always issues a fresh
// POST (the handler dedupes concurrent invocations).
//
// FR-CHK3-FORM-STATE: form state is cleared on `all_passed === true`.

import { createApp, reactive } from '/static/vendor/petite-vue.js';
import {
  consumeResumeBanner,
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';
import { clearAllFormState } from '/static/js/form_state.js';
import {
  STEP_VERIFY,
  backRouteFor,
  buildStepperModel,
  fetchChromeContext,
} from '/static/js/wizard_chrome.js';

const CHECK_LABELS = {
  network_bindable: 'Network bind succeeds',
  database_reachable: 'Database is reachable',
  ffmpeg_runs: 'FFmpeg runs and reports the expected version',
  pending_setup_writable: 'Manager data directory is writable',
  worker_password_hashed: 'Worker UI password is set',
};

const scope = reactive({
  checks: [],
  allPassed: false,
  running: false,
  error: '',
  resumeBanner: '',
  topology: null,
  stepper: buildStepperModel(
    null, STEP_VERIFY,
    [
      'topology_chosen', 'network_configured',
      'database_configured', 'admin_validated', 'ffmpeg_installed',
    ],
  ),

  goBack() {
    const route = backRouteFor(STEP_VERIFY, this.topology);
    if (route) {
      window.location.assign(route);
    }
  },

  labelFor(name) {
    return CHECK_LABELS[name] || name;
  },

  async run() {
    this.running = true;
    this.error = '';
    let response;
    try {
      response = await wizardFetch('/api/wizard/verify/', { method: 'POST' });
    } catch (_) {
      this.running = false;
      this.error = 'Network error. Check the launcher logs and try again.';
      return;
    }
    if (response.status === 401 || response.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    let body;
    try { body = await response.json(); }
    catch (_) {
      this.running = false;
      this.error = 'Could not read the verify response.';
      return;
    }
    this.running = false;
    if (!response.ok) {
      this.error = (body && body.error) || 'Verify could not run.';
      return;
    }
    if (Array.isArray(body.checks)) {
      this.checks = body.checks;
    }
    this.allPassed = !!body.all_passed;
    if (this.allPassed) {
      // FR-CHK3-FORM-STATE: clear form state past the verify boundary.
      clearAllFormState();
    }
  },

  next() {
    if (!this.allPassed) return;
    window.location.assign('/done');
  },
});

document.addEventListener('DOMContentLoaded', () => {
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  createApp(scope).mount('#app');
  scope.run();
  // Topology-dependent completed list — see ffmpeg.js for the same
  // pattern. Verify is reached only after FFmpeg, so ffmpeg_installed
  // is also complete.
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
    completed.push('ffmpeg_installed');
    scope.stepper = buildStepperModel(ctx.topology, STEP_VERIFY, completed);
  })();
});
