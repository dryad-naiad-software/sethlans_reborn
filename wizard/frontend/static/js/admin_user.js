// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Admin user page (admin-user.html) controller (FR-M2-5).
//
// Inline validation: password mismatch checked client-side; the
// backend's validator rejects (length, similarity, common, all-numeric)
// with HTTP 400 + a `failures` array which we surface verbatim so the
// user sees the exact reasons.
//
// On success, the admin password is POSTed to /api/wizard/admin-user/
// where the wizard backend stashes it in its in-memory wizard_state
// module (FR-M2-5). The worker-password page does NOT receive the
// password back through the browser — when the operator checks "Use
// admin password" there, the worker-password handler reads the admin
// plaintext from wizard_state server-side. This avoids the FE-1
// sessionStorage leak (admin password was previously written to
// `wizard:adminPasswordTransient` which lingered until tab close).
//
// The admin password therefore lives only in this Petite-vue scope's
// `form.password` for the lifetime of the admin-user page; on
// navigation the JS context is torn down and the reference is GC'd.
// NEVER stashed in the regular form-state stash (which is for
// re-display on Back; passwords are excluded per FR-CHK3-FORM-STATE).

import { createApp, reactive } from '/static/vendor/petite-vue.js';
import {
  consumeResumeBanner,
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';
import {
  loadFormState,
  stashFormState,
} from '/static/js/form_state.js';
import {
  STEP_ADMIN,
  applyChromeContext,
  backRouteFor,
  buildStepperModel,
} from '/static/js/wizard_chrome.js';
import {
  evaluatePassword,
  strengthClass,
} from '/static/js/password_strength.js';

const STEP = 'admin-user';

const FAILURE_LABELS = {
  password_too_short: 'Password must be at least 8 characters.',
  password_too_long: 'Password is too long.',
  password_too_similar: 'Password is too similar to the username or email.',
  password_too_common: 'Password is too common.',
  password_all_numeric: 'Password cannot be all digits.',
};

const scope = reactive({
  form: { username: '', email: '', password: '', passwordConfirm: '' },
  submitting: false,
  error: '',
  fieldError: '',
  failures: [],
  resumeBanner: '',
  topology: null,
  // Single eye-toggle state shared by both password fields — clicking
  // either eye reveals/hides BOTH inputs so the user can verify the
  // confirm matches without an extra click.
  showPassword: false,
  stepper: buildStepperModel(
    null, STEP_ADMIN,
    ['topology_chosen', 'network_configured', 'database_configured'],
  ),

  get strength() {
    return evaluatePassword(
      this.form.password, [this.form.username, this.form.email],
    );
  },
  get strengthClassName() {
    const s = this.strength;
    return strengthClass(s.score, s.max);
  },
  // Live confirm-match indicator. Returns null when confirm is empty
  // (so we render nothing) and a boolean once the user has typed.
  get passwordsMatch() {
    if (!this.form.passwordConfirm) return null;
    return this.form.password === this.form.passwordConfirm;
  },

  goBack() {
    const route = backRouteFor(STEP_ADMIN, this.topology);
    if (route) {
      this._stash();
      window.location.assign(route);
    }
  },

  _stash() {
    // Passwords NEVER stashed in the form-state stash.
    stashFormState(STEP, {
      username: this.form.username,
      email: this.form.email,
    });
  },

  async submit() {
    if (this.submitting) return;
    if (!this.form.username || !this.form.email
        || !this.form.password || !this.form.passwordConfirm) {
      this.error = 'Please fill in every field.';
      return;
    }
    if (this.form.password !== this.form.passwordConfirm) {
      this.error = 'Passwords do not match.';
      this.fieldError = 'password_mismatch';
      return;
    }
    this.error = '';
    this.fieldError = '';
    this.failures = [];
    this.submitting = true;
    this._stash();
    let response;
    try {
      response = await wizardFetch('/api/wizard/admin-user/', {
        method: 'POST',
        body: JSON.stringify({
          username: this.form.username,
          email: this.form.email,
          password: this.form.password,
          password_confirm: this.form.passwordConfirm,
        }),
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
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* tolerate */ }
    if (response.status === 200) {
      // The wizard backend has stashed the admin tuple in its
      // in-memory wizard_state. The browser does NOT round-trip the
      // password to /worker-password — the worker-password handler
      // reads the admin plaintext from wizard_state server-side when
      // the "Use admin password" checkbox is on (FR-M2-6). The
      // worker-password page is always navigated to next; for manager
      // topology that page detects "no worker on this machine" and
      // forwards to /ffmpeg automatically.
      window.location.assign('/worker-password');
      return;
    }
    if (response.status === 500 && payload && payload.error === 'common_passwords_resource_invalid') {
      this.submitting = false;
      this.error = 'The wizard could not load its password validator data. Restart the launcher.';
      return;
    }
    this.submitting = false;
    if (payload && payload.error === 'password_invalid' && Array.isArray(payload.failures)) {
      this.fieldError = 'password_invalid';
      this.failures = payload.failures.map(f => FAILURE_LABELS[f] || f);
      this.error = 'Password did not meet the requirements.';
      return;
    }
    if (payload && typeof payload.error === 'string') {
      this.fieldError = payload.error;
      this.error = 'Please correct the highlighted field.';
      return;
    }
    this.error = 'Could not validate the admin user. Check the launcher logs.';
  },
});

document.addEventListener('DOMContentLoaded', () => {
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  const stash = loadFormState(STEP);
  if (stash) {
    if (typeof stash.username === 'string') scope.form.username = stash.username;
    if (typeof stash.email === 'string') scope.form.email = stash.email;
  }
  createApp(scope).mount('#app');
  const f = document.getElementById('admin-username');
  if (f) f.focus();
  applyChromeContext(scope, STEP_ADMIN, [
    'topology_chosen', 'network_configured', 'database_configured',
  ]);
});
