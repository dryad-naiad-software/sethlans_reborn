// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Worker UI password page (worker-password.html) controller (FR-M2-6).
//
// Skipped when topology is `manager` — on mount we ask the
// resume-target endpoint for topology; if manager (no co-located
// worker) we forward to /ffmpeg and never render the form.
//
// Pre-checked "Use the admin password" checkbox: when on, we re-send
// the admin password held transiently in sessionStorage from the
// admin-user step. The wizard never reads admin password back from
// pending_setup.json; the frontend round-trips it.

import { createApp } from '/static/vendor/petite-vue.js';
import {
  consumeResumeBanner,
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';

const ADMIN_PW_KEY = 'wizard:adminPasswordTransient';

function readAdminPassword() {
  try { return window.sessionStorage.getItem(ADMIN_PW_KEY) || ''; }
  catch (_) { return ''; }
}

const scope = {
  useAdminPassword: true,
  password: '',
  submitting: false,
  error: '',
  fieldError: '',
  resumeBanner: '',
  adminPwAvailable: false,

  async submit() {
    if (this.submitting) return;
    this.error = '';
    this.fieldError = '';
    let pw;
    if (this.useAdminPassword) {
      pw = readAdminPassword();
      if (!pw) {
        this.error = 'The admin password is not available in this tab. '
          + 'Uncheck the box and enter a password directly.';
        return;
      }
    } else {
      if (!this.password) {
        this.fieldError = 'password_required';
        return;
      }
      if (this.password.length < 8) {
        this.fieldError = 'password_too_short';
        return;
      }
      pw = this.password;
    }
    this.submitting = true;
    let response;
    try {
      response = await wizardFetch('/api/wizard/worker-password/', {
        method: 'POST',
        body: JSON.stringify({
          password: pw,
          use_admin_password: this.useAdminPassword,
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
    if (response.status === 200) {
      window.location.assign('/ffmpeg');
      return;
    }
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* tolerate */ }
    this.submitting = false;
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
};

document.addEventListener('DOMContentLoaded', () => {
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  scope.adminPwAvailable = !!readAdminPassword();
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
          window.location.replace('/ffmpeg');
          return;
        }
      }
    } catch (_) { /* tolerated; render the page */ }
    createApp(scope).mount('#app');
    const checkbox = document.getElementById('use-admin-checkbox');
    if (checkbox) checkbox.focus();
  })();
});
