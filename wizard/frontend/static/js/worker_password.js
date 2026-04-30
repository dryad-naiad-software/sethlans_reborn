// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Worker UI password page (worker-password.html) controller (FR-M2-6).
//
// Skipped when topology is `manager` — on mount we ask the
// resume-target endpoint for topology; if manager (no co-located
// worker) we forward to /verify and never render the form.
//
// Pre-checked "Use the admin password" checkbox: when on, the POST
// body carries `{use_admin_password: true}` with no password field.
// The wizard backend reads the admin plaintext from its in-memory
// wizard_state module (FR-M2-5) and uses it as the worker password
// input. The browser never holds the admin password — earlier Phase 2
// builds round-tripped it through `window.sessionStorage` which the
// frontend-reviewer flagged FE-1 as a plaintext-credential leak. If
// the admin step was never completed (process restart between steps)
// the backend returns HTTP 400 `admin_password_unavailable` and we
// surface a recovery prompt.

import { createApp, reactive } from '/static/vendor/petite-vue.js';
import {
  consumeResumeBanner,
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';
import {
  STEP_WORKER,
  applyChromeContext,
  backRouteFor,
  buildStepperModel,
} from '/static/js/wizard_chrome.js';
import {
  evaluatePassword,
  strengthClass,
} from '/static/js/password_strength.js';

const scope = reactive({
  useAdminPassword: true,
  password: '',
  passwordConfirm: '',
  submitting: false,
  error: '',
  fieldError: '',
  resumeBanner: '',
  topology: 'manager_worker',
  // Single eye-toggle state shared by both worker password fields —
  // mirrors the admin-user pattern. The fields only render when
  // `useAdminPassword` is unchecked.
  showPassword: false,
  stepper: buildStepperModel(
    'manager_worker', STEP_WORKER,
    [
      'topology_chosen', 'network_configured',
      'database_configured', 'admin_validated',
    ],
  ),

  get strength() {
    // Worker page has no username/email context — pass empty attrs so
    // the similarity check is omitted rather than rendered as always-pass.
    return evaluatePassword(this.password, []);
  },
  get strengthClassName() {
    const s = this.strength;
    return strengthClass(s.score, s.max);
  },
  get passwordsMatch() {
    if (!this.passwordConfirm) return null;
    return this.password === this.passwordConfirm;
  },

  goBack() {
    const route = backRouteFor(STEP_WORKER, this.topology);
    if (route) {
      window.location.assign(route);
    }
  },

  async submit() {
    if (this.submitting) return;
    this.error = '';
    this.fieldError = '';
    let body;
    if (this.useAdminPassword) {
      // No password field — backend reads the admin plaintext from
      // wizard_state. Sending `password: null` for explicitness;
      // the handler inspects `use_admin_password` first.
      body = { use_admin_password: true, password: null };
    } else {
      if (!this.password) {
        this.fieldError = 'password_required';
        return;
      }
      if (this.password.length < 8) {
        this.fieldError = 'password_too_short';
        return;
      }
      if (this.password !== this.passwordConfirm) {
        this.error = 'Passwords do not match.';
        this.fieldError = 'password_mismatch';
        return;
      }
      body = { use_admin_password: false, password: this.password };
    }
    this.submitting = true;
    let response;
    try {
      response = await wizardFetch('/api/wizard/worker-password/', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    } catch (_) {
      this.submitting = false;
      this.error = 'Network error. Check the launcher logs and try again.';
      return;
    }
    if (response.status === 401 || response.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (response.status === 200) {
      window.location.assign('/verify');
      return;
    }
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* tolerate */ }
    this.submitting = false;
    if (payload && payload.error === 'admin_password_unavailable') {
      this.error = 'The admin password is not available — uncheck the box '
        + 'and enter a worker password directly.';
      return;
    }
    if (payload && payload.error === 'password_too_short') {
      this.fieldError = 'password_too_short';
      return;
    }
    if (payload && payload.error === 'password_required') {
      this.fieldError = 'password_required';
      return;
    }
    this.error = 'Could not save the worker password. Check the launcher logs.';
  },
});

document.addEventListener('DOMContentLoaded', () => {
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  // If topology is manager-only, skip this page entirely.
  (async () => {
    try {
      const r = await wizardFetch('/api/wizard/resume-target/');
      if (r.status === 401 || r.status === 403) {
        expireAndRedirect(
          'Your session expired — please paste the setup token again.',
        );
        return;
      }
      if (r.ok) {
        const body = await r.json();
        if (body && body.topology === 'manager') {
          window.location.replace('/verify');
          return;
        }
      }
    } catch (_) { /* tolerated; render the page */ }
    createApp(scope).mount('#app');
    const checkbox = document.getElementById('use-admin-checkbox');
    if (checkbox) checkbox.focus();
    applyChromeContext(scope, STEP_WORKER, [
      'topology_chosen', 'network_configured',
      'database_configured', 'admin_validated',
    ]);
  })();
});
