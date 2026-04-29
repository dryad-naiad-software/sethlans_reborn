// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Done page (done.html) controller (FR-M2-9).
//
// On mount, in order (mandatory):
//   1. POST /api/wizard/pending-setup/  — atomic write of pending_setup.json
//   2. POST /api/wizard/done/           — writes the .wizard_done IPC marker
//   3. window.location.assign('/redirecting')
//
// We only navigate to /redirecting once BOTH POSTs return 200; if
// either fails, we surface the error with a Retry button.

import { createApp } from '/static/vendor/petite-vue.js';
import {
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';
import { clearAllFormState } from '/static/js/form_state.js';

const STAGE_MESSAGES = {
  pending: 'Writing setup file&hellip;',
  done: 'Signalling the launcher&hellip;',
  redirecting: 'Redirecting&hellip;',
};

const scope = {
  stage: 'pending',
  error: '',
  get busy() { return !this.error; },
  get stageMessage() {
    return STAGE_MESSAGES[this.stage] || STAGE_MESSAGES.pending;
  },

  async run() {
    this.error = '';
    this.stage = 'pending';
    let response;
    try {
      response = await wizardFetch('/api/wizard/pending-setup/', { method: 'POST' });
    } catch (_) {
      this.error = 'Network error writing the pending-setup file. Try again.';
      return;
    }
    if (response.status === 401 || response.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (!response.ok) {
      this.error = 'Could not write the pending-setup file. Check the launcher logs.';
      return;
    }
    this.stage = 'done';
    let doneResponse;
    try {
      doneResponse = await wizardFetch('/api/wizard/done/', { method: 'POST' });
    } catch (_) {
      this.error = 'Network error signalling the launcher. Try again.';
      return;
    }
    if (doneResponse.status === 401 || doneResponse.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (doneResponse.status !== 200) {
      this.error = 'The wizard could not signal the launcher. Check the launcher logs.';
      return;
    }
    this.stage = 'redirecting';
    // Form-state stash is no longer useful past this point.
    clearAllFormState();
    window.history.replaceState({}, '', '/redirecting');
    window.location.assign('/redirecting');
  },
};

document.addEventListener('DOMContentLoaded', () => {
  createApp(scope).mount('#app');
  scope.run();
});
