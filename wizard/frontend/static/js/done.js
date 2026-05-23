// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Done page (done.html) controller (FR-M2-9).
//
// On mount, in order (mandatory):
//   1. POST /api/wizard/pending-setup/  — atomic write of pending_setup.json
//   2. POST /api/wizard/done/           — writes the .wizard_done IPC marker
//
// On success, display the success card. The launcher opens the dashboard
// tab via open_browser(); the wizard tab stays on the success card.
// A transport error on the done POST is expected: the launcher's
// file-poll loop sees the .wizard_done marker and kills Caddy before
// the response flushes. Pending-setup 200 confirms the marker was
// written — treat the transport error as success.

import { createApp, reactive } from '/static/vendor/petite-vue.js';
import {
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';
import { clearAllFormState } from '/static/js/form_state.js';
import {
  STEP_DONE,
  buildStepperModel,
  fetchChromeContext,
} from '/static/js/wizard_chrome.js';

const STAGE_MESSAGES = {
  pending: 'Writing setup file&hellip;',
  done: 'Signalling the launcher&hellip;',
};

const scope = reactive({
  stage: 'pending',
  error: '',
  success: false,
  // Done is the last step. Mark every previous checkpoint as complete
  // in the stepper so the user sees the full trail filled in. The
  // current entry (Done) is the highlighted one.
  topology: null,
  stepper: buildStepperModel(
    null, STEP_DONE,
    [
      'topology_chosen', 'network_configured', 'database_configured',
      'admin_validated', 'worker_password_set',
      'verified',
    ],
  ),
  get busy() { return !this.error && !this.success; },
  get stageMessage() {
    return STAGE_MESSAGES[this.stage] || STAGE_MESSAGES.pending;
  },

  async run() {
    this.error = '';
    this.success = false;
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
      // Transport error on the done POST is expected: the launcher's
      // file-poll loop sees the .wizard_done marker and kills Caddy
      // before the response flushes. Pending-setup 200 confirms the
      // marker was written. Treat as success — the launcher opens the
      // dashboard tab on its own.
      clearAllFormState();
      this.success = true;
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
    // Form-state stash is no longer useful past this point.
    clearAllFormState();
    this.success = true;
  },
});

document.addEventListener('DOMContentLoaded', () => {
  createApp(scope).mount('#app');
  scope.run();
  // Done is the terminal step; everything before it is completed.
  // Worker_password_set only applies to manager_worker topology.
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
    completed.push('verified');
    scope.stepper = buildStepperModel(ctx.topology, STEP_DONE, completed);
  })();
});
