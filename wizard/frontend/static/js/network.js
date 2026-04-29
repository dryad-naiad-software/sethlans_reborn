// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Network page (network.html) controller (FR-M2-3).
//
// Maps the Yes/No choice to bind_host = 0.0.0.0 vs 127.0.0.1.
// Advanced options expose bind_port (default 8080) + data_dir override.
// Maps the backend's data_dir_invalid category to a user-readable
// message. Form state stashed in sessionStorage per FR-CHK3-FORM-STATE
// (no password fields on this page, so the whole form is safe to stash).

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

const STEP = 'network';

const DATA_DIR_MESSAGES = {
  relative: 'Data directory must be an absolute path.',
  traversal: 'Data directory cannot contain ".." segments.',
  forbidden_root: 'Data directory cannot live under a system directory.',
  device_namespace: 'Data directory cannot use a Windows device namespace.',
};

const scope = {
  choices: [
    {
      id: 'yes', value: true,
      label: 'Yes — allow LAN workers',
      desc: 'Bind 0.0.0.0 so workers on the network can connect.',
    },
    {
      id: 'no', value: false,
      label: 'No — this machine only',
      desc: 'Bind 127.0.0.1; only worker(s) on this same machine can connect.',
    },
  ],
  allowExternal: null,
  advancedOpen: false,
  bindPort: 8080,
  dataDir: '',
  submitting: false,
  error: '',
  fieldError: null,
  resumeBanner: '',

  selectChoice(value) {
    this.allowExternal = value;
    this.error = '';
    this.fieldError = null;
    this._stash();
  },

  _stash() {
    stashFormState(STEP, {
      allowExternal: this.allowExternal,
      advancedOpen: this.advancedOpen,
      bindPort: this.bindPort,
      dataDir: this.dataDir,
    });
  },

  _abortWithError(message, fieldError) {
    this.submitting = false;
    this.error = message;
    if (fieldError !== undefined) this.fieldError = fieldError;
  },

  async submit() {
    if (this.submitting) return;
    if (this.allowExternal === null) {
      this.error = 'Please choose Yes or No.';
      return;
    }
    if (typeof this.bindPort !== 'number' || !Number.isInteger(this.bindPort)
        || this.bindPort < 1 || this.bindPort > 65535) {
      this._abortWithError('Bind port must be an integer between 1 and 65535.', 'bind_port');
      return;
    }
    this.error = '';
    this.fieldError = null;
    this.submitting = true;
    this._stash();
    const body = {
      bind_host: this.allowExternal ? '0.0.0.0' : '127.0.0.1',
      bind_port: this.bindPort,
      data_dir: this.dataDir.length > 0 ? this.dataDir : null,
    };
    let response;
    try {
      response = await wizardFetch('/api/wizard/network/', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    } catch (_) {
      this._abortWithError(
        'Network error. Check the launcher logs and try again.',
      );
      return;
    }
    if (response.status === 401 || response.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (response.status === 200) {
      window.location.assign('/database');
      return;
    }
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* shape-tolerant */ }
    if (payload && payload.error === 'data_dir_invalid') {
      const cat = (payload.category && DATA_DIR_MESSAGES[payload.category])
        || 'Data directory is invalid.';
      this._abortWithError(cat, 'data_dir');
      return;
    }
    if (payload && payload.error === 'bind_failed') {
      this._abortWithError(
        'Could not bind to the chosen host/port. Try a different port.',
        'bind_port',
      );
      return;
    }
    this._abortWithError(
      'Could not save network settings. Check the launcher logs and try again.',
    );
  },
};

function focusByIndex(idx) {
  const cards = document.querySelectorAll('.topology-radiogroup [role="radio"]');
  const target = cards[Math.max(0, Math.min(idx, cards.length - 1))];
  if (target) target.focus();
}

function attachKeyboardHandler() {
  const group = document.querySelector('.topology-radiogroup');
  if (!group) return;
  group.addEventListener('keydown', (event) => {
    const target = event.target.closest('[role="radio"]');
    if (!target) return;
    const cards = Array.from(document.querySelectorAll('.topology-radiogroup [role="radio"]'));
    const idx = cards.indexOf(target);
    const total = scope.choices.length;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      const next = (idx + 1) % total;
      scope.selectChoice(scope.choices[next].value);
      requestAnimationFrame(() => focusByIndex(next));
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      const prev = (idx - 1 + total) % total;
      scope.selectChoice(scope.choices[prev].value);
      requestAnimationFrame(() => focusByIndex(prev));
    } else if (event.key === 'Home') {
      event.preventDefault();
      scope.selectChoice(scope.choices[0].value);
      requestAnimationFrame(() => focusByIndex(0));
    } else if (event.key === 'End') {
      event.preventDefault();
      scope.selectChoice(scope.choices[total - 1].value);
      requestAnimationFrame(() => focusByIndex(total - 1));
    } else if (event.key === ' ' || event.key === 'Enter') {
      event.preventDefault();
      const choice = scope.choices[idx];
      if (choice) scope.selectChoice(choice.value);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  const stash = loadFormState(STEP);
  if (stash) {
    if (typeof stash.allowExternal === 'boolean') scope.allowExternal = stash.allowExternal;
    if (typeof stash.advancedOpen === 'boolean') scope.advancedOpen = stash.advancedOpen;
    if (Number.isInteger(stash.bindPort)) scope.bindPort = stash.bindPort;
    if (typeof stash.dataDir === 'string') scope.dataDir = stash.dataDir;
  }
  createApp(scope).mount('#app');
  attachKeyboardHandler();
});
