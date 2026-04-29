// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Welcome page (welcome.html) controller (FR-M2-1).
//
// On Next: POST /api/wizard/welcome/ to record the welcome_seen
// checkpoint, then navigate to /topology.

import { createApp } from '/static/vendor/petite-vue.js';
import {
  consumeResumeBanner,
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';

const scope = {
  submitting: false,
  error: '',
  resumeBanner: '',

  async next() {
    if (this.submitting) return;
    this.error = '';
    this.submitting = true;
    let response;
    try {
      response = await wizardFetch('/api/wizard/welcome/', { method: 'POST' });
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
    if (response.status !== 200) {
      this.submitting = false;
      this.error = 'Could not record progress. Check the launcher logs and try again.';
      return;
    }
    window.location.assign('/topology');
  },
};

document.addEventListener('DOMContentLoaded', () => {
  const banner = consumeResumeBanner();
  if (banner) {
    scope.resumeBanner = banner;
  }
  createApp(scope).mount('#app');
  const btn = document.getElementById('welcome-next');
  if (btn) btn.focus();
});
