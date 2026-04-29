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
// On success, the next page depends on topology — manager_worker goes
// to /worker-password; manager goes straight to /ffmpeg. We persist
// the admin password (in process memory only via sessionStorage's
// short lifetime) so the worker-password page can re-use it when the
// "use admin password" checkbox is checked. NEVER stashed in the
// regular form-state stash (which is for re-display on Back).

import { createApp } from '/static/vendor/petite-vue.js';
import {
  consumeResumeBanner,
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';
import {
  loadFormState,
  stashFormState,
} from '/static/js/form_state.js';

const STEP = 'admin-user';

// Holding the admin password between admin-user and worker-password
// pages requires a separate sessionStorage key — the regular form
// stash is opaque (per FR-CHK3-FORM-STATE, no passwords). This is the
// minimum lifetime required by FR-M2-6's "use admin password"
// affordance: the user enters it once on admin-user.html and the
// worker-password page re-sends it. Lives only until pending-setup
// fires, then cleared.
const ADMIN_PW_KEY = 'wizard:adminPasswordTransient';

const FAILURE_LABELS = {
  password_too_short: 'Password must be at least 8 characters.',
  password_too_long: 'Password is too long.',
  password_too_similar: 'Password is too similar to the username or email.',
  password_too_common: 'Password is too common.',
  password_all_numeric: 'Password cannot be all digits.',
};

const scope = {
  form: { username: '', email: '', password: '', passwordConfirm: '' },
  submitting: false,
  error: '',
  fieldError: '',
  failures: [],
  resumeBanner: '',
  topology: null,

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
      // Save the password transiently so the worker-password page can
      // reuse it when the "use admin password" checkbox is on. The
      // worker-password page is always navigated to next; for manager
      // topology that page detects "no worker on this machine" and
      // forwards to /ffmpeg automatically.
      try { window.sessionStorage.setItem(ADMIN_PW_KEY, this.form.password); }
      catch (_) { /* storage may be disabled */ }
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
};

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
});
