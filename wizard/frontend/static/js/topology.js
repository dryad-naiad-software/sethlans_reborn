// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Topology picker page (topology.html) controller.
//
// FR-W-FE4 / FR-W-FE9 contract:
//   1. POST /api/wizard/topology/ with the chosen topology
//   2. On 200, POST /api/wizard/done/ — this is the IPC trigger that
//      writes .wizard_done so the launcher spawns the runtime
//   3. On the response from /done/ (200 OR `{"status":"already_done"}`
//      per FR-W9a idempotency), navigate to /redirecting
//
// HIGH-1 fix: the original handler skipped step 2, leaving every
// first-time setup hung at /redirecting forever. Without /done/ the
// launcher never sees the IPC marker and never spawns the runtime.
//
// Keyboard handling implements the full WAI-ARIA radiogroup pattern:
// arrows + Space/Enter + Home/End (MEDIUM-3). The handler is attached
// to the radiogroup container after Petite-vue mounts so the cards
// already exist in the DOM (integration pattern point 4).

import { createApp } from '/static/vendor/petite-vue.js';
import {
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';

const scope = {
  choices: [
    { id: 'manager', label: 'Manager only',
      desc: 'Run as a render farm controller. Workers connect to this machine.' },
    { id: 'manager_worker', label: 'Manager + Worker',
      desc: 'This machine controls the farm AND renders jobs.' },
    { id: 'worker_only', label: 'Worker only',
      desc: 'Connect to an existing manager and render jobs for it.' },
  ],
  selected: null,
  submitting: false,
  error: '',

  selectChoice(id) {
    this.selected = id;
    this.error = '';
  },

  // Helper: reset the in-flight UI state and surface a generic error.
  // Used on network failures and on /done/ failures so the user can
  // retry without losing their selection.
  _abortWithError(message) {
    this.submitting = false;
    this.error = message;
  },

  async submit() {
    if (this.submitting || this.selected === null) {
      return;
    }
    this.error = '';
    this.submitting = true;

    // Step 1 — record the topology choice.
    let topologyResponse;
    try {
      topologyResponse = await wizardFetch('/api/wizard/topology/', {
        method: 'POST',
        body: JSON.stringify({ topology: this.selected }),
      });
    } catch (_) {
      this._abortWithError(
        'Network error. Check the launcher logs and try again.',
      );
      return;
    }

    if (topologyResponse.status === 401 || topologyResponse.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (topologyResponse.status !== 200) {
      this._abortWithError(
        'Something went wrong saving your topology. Check the launcher logs and try again.',
      );
      return;
    }

    // Step 2 — fire /done/ to trigger the IPC marker. WITHOUT this the
    // launcher never spawns the runtime (HIGH-1, FR-W-FE4 + FR-W9a).
    let doneResponse;
    try {
      doneResponse = await wizardFetch('/api/wizard/done/', {
        method: 'POST',
      });
    } catch (_) {
      this._abortWithError(
        'Network error finalizing setup. Check the launcher logs and try again.',
      );
      return;
    }

    if (doneResponse.status === 401 || doneResponse.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }

    // FR-W9a: /done/ is idempotent. A 200 with status "already_done"
    // means "the IPC marker already exists" — treat it as success and
    // continue to the wait page.
    if (doneResponse.status === 200) {
      // Replace history so Back does not return the user here and
      // re-fire /done/ unnecessarily — MEDIUM-2.
      window.history.replaceState({}, '', '/redirecting');
      window.location.assign('/redirecting');
      return;
    }

    this._abortWithError(
      'Something went wrong finalizing setup. Check the launcher logs and try again.',
    );
  },
};

function focusByIndex(idx) {
  const cards = document.querySelectorAll('[role="radio"]');
  if (!cards.length) return;
  const target = cards[Math.max(0, Math.min(idx, cards.length - 1))];
  if (target) target.focus();
}

function findCardIndex(el) {
  const cards = Array.from(document.querySelectorAll('[role="radio"]'));
  return cards.indexOf(el);
}

function attachKeyboardHandler() {
  const group = document.querySelector('.topology-radiogroup');
  if (!group) return;
  group.addEventListener('keydown', (event) => {
    const target = event.target.closest('[role="radio"]');
    if (!target) return;
    const idx = findCardIndex(target);
    if (idx < 0) return;
    const total = scope.choices.length;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      const next = (idx + 1) % total;
      scope.selectChoice(scope.choices[next].id);
      requestAnimationFrame(() => focusByIndex(next));
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      const prev = (idx - 1 + total) % total;
      scope.selectChoice(scope.choices[prev].id);
      requestAnimationFrame(() => focusByIndex(prev));
    } else if (event.key === 'Home') {
      // MEDIUM-3 — WAI-ARIA radiogroup pattern: Home → first card.
      event.preventDefault();
      scope.selectChoice(scope.choices[0].id);
      requestAnimationFrame(() => focusByIndex(0));
    } else if (event.key === 'End') {
      // MEDIUM-3 — WAI-ARIA radiogroup pattern: End → last card.
      event.preventDefault();
      scope.selectChoice(scope.choices[total - 1].id);
      requestAnimationFrame(() => focusByIndex(total - 1));
    } else if (event.key === ' ' || event.key === 'Enter') {
      event.preventDefault();
      const choice = scope.choices[idx];
      if (choice) scope.selectChoice(choice.id);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  createApp(scope).mount('#app');
  // Attach the radiogroup keyboard handler after Petite-vue mounts so
  // the cards exist in the DOM (FR-W-FE9 + integration pattern point 4
  // — manual handler, not data-bs-* attributes).
  attachKeyboardHandler();
});
