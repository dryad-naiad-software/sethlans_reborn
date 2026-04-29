// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Topology picker page (topology.html) controller.
//
// Spec 2 / FR-M2-2 contract (replaces Spec 1 behaviour):
//   1. POST /api/wizard/topology/ with the chosen topology.
//   2. On 200, navigate to /network for `manager` and `manager_worker`,
//      or /find-manager for `worker_only` (the latter is Spec 3
//      territory; the route is currently a 404 — leaving the
//      navigation as a TODO marker so Spec 3 plugs the page in).
//
// The wizard `done` endpoint is NOT fired here anymore. It is fired
// only from the verify / done step (FR-M2-9).
//
// Keyboard handling implements the full WAI-ARIA radiogroup pattern:
// arrows + Space/Enter + Home/End. The handler is attached to the
// radiogroup container after Petite-vue mounts so the cards already
// exist in the DOM.

import { createApp, reactive } from '/static/vendor/petite-vue.js';
import {
  consumeResumeBanner,
  expireAndRedirect,
  wizardFetch,
} from '/static/js/common.js';
import {
  STEP_TOPOLOGY,
  buildStepperModel,
  fetchChromeContext,
} from '/static/js/wizard_chrome.js';

const scope = reactive({
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
  resumeBanner: '',
  // Stepper model — populated on mount once /resume-target reports the
  // active topology. Falls back to a manager-flavoured stepper if the
  // probe is unavailable so the chrome still renders.
  stepper: buildStepperModel(null, STEP_TOPOLOGY, []),

  selectChoice(id) {
    this.selected = id;
    this.error = '';
  },

  async _loadChromeContext() {
    const ctx = await fetchChromeContext();
    this.topology = ctx.topology;
    // Topology is the first stepper step — no prior checkpoints have
    // been recorded by the time the user lands here.
    this.stepper = buildStepperModel(ctx.topology, STEP_TOPOLOGY, []);
  },

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

    // Spec 2 — navigate based on topology. The wizard `done` endpoint
    // is NOT fired here anymore (FR-M2-2); that is the verify/done
    // step's job.
    if (this.selected === 'worker_only') {
      // TODO(Phase 3 / Spec 3): /find-manager isn't shipped yet. The
      // route 404s; this is intentional placeholder navigation that
      // Spec 3 will fill in.
      window.history.replaceState({}, '', '/find-manager');
      window.location.assign('/find-manager');
      return;
    }
    window.history.replaceState({}, '', '/network');
    window.location.assign('/network');
  },
});

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
      event.preventDefault();
      scope.selectChoice(scope.choices[0].id);
      requestAnimationFrame(() => focusByIndex(0));
    } else if (event.key === 'End') {
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
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  const app = createApp(scope);
  app.mount('#app');
  attachKeyboardHandler();
  // Resolve topology once Petite-vue has mounted so the stepper picks
  // up the manager_worker variant if the user already chose that
  // topology in a prior session and is now re-visiting. Calling the
  // method on `scope` goes through the reactive proxy Petite-vue
  // installed in-place, so the v-for in the header re-renders.
  scope._loadChromeContext();
});
