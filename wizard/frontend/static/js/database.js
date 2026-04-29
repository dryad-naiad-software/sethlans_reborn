// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Database page (database.html) controller (FR-M2-4 / FR-FE-DB-DISCLOSURE).
//
// Engine selector + progressive disclosure: only the database name/path
// shows for sqlite; selecting postgresql/mysql/custom reveals host/port/
// user/password; custom additionally reveals an engine_path field.
//
// Backend error categories map to user-readable messages here. Raw
// driver text never reaches the user (security-reviewer MED-3 — the
// backend's HTTP body already redacts; this layer is just translation).
//
// Form state stashed sans password (FR-CHK3-FORM-STATE).

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

const STEP = 'database';

// Canonical default port per engine. Authoritative on load and on
// every engine swap — the port is NEVER stashed in sessionStorage,
// so a user who picks postgresql then switches to mysql gets 3306,
// not a stale 5432 left over from the previous engine (FE-5 fix).
// User overrides during the live form session do persist (in scope
// state) but are dropped on reload to keep the engine/port pairing
// honest.
const DEFAULT_PORTS = Object.freeze({
  postgresql: 5432,
  mysql: 3306,
});

const ERROR_MESSAGES = {
  auth_failed: 'Authentication failed. Check the user and password.',
  host_unreachable: 'Could not reach the database host. Check the address and that the server is running.',
  db_not_found: 'The database name was not found on the server.',
  permission_denied: 'The user does not have permission for that database.',
  ssl_error: 'SSL handshake failed.',
  timeout: 'Connection timed out.',
  generic: 'Could not connect to the database.',
};

const scope = {
  engines: [
    { id: 'sqlite', label: 'SQLite', desc: 'Default. Single-file embedded database.' },
    { id: 'postgresql', label: 'PostgreSQL', desc: 'External Postgres server.' },
    { id: 'mysql', label: 'MySQL / MariaDB', desc: 'External MySQL or MariaDB server.' },
    { id: 'custom', label: 'Custom', desc: 'User-supplied Django backend module.' },
  ],
  engine: 'sqlite',
  form: { name: '', host: '', port: 5432, user: '', password: '', enginePath: '' },
  submitting: false,
  error: '',
  canRetry: false,
  resumeBanner: '',

  selectEngine(id) {
    this.engine = id;
    this.error = '';
    this.canRetry = false;
    // Always snap port to the new engine's canonical default. We
    // intentionally do NOT preserve the previous engine's port — the
    // typical case (user clicks pg then switches to mysql) was leaving
    // 5432 behind, which is the wrong default for mysql (FE-5).
    if (DEFAULT_PORTS[id] !== undefined) {
      this.form.port = DEFAULT_PORTS[id];
    }
    this._stash();
  },

  _stash() {
    // Password fields are NEVER stashed (FR-CHK3-FORM-STATE).
    // Port is NOT stashed either — it is always recomputed from the
    // engine on load via DEFAULT_PORTS. See selectEngine for the
    // engine-swap rationale (FE-5).
    stashFormState(STEP, {
      engine: this.engine,
      name: this.form.name,
      host: this.form.host,
      user: this.form.user,
      enginePath: this.form.enginePath,
    });
  },

  _abort(message, retryable) {
    this.submitting = false;
    this.error = message;
    this.canRetry = !!retryable;
  },

  async submit() {
    if (this.submitting) return;
    if (this.engine === null) return;
    this.error = '';
    this.canRetry = false;
    this.submitting = true;
    this._stash();
    const body = { engine: this.engine, name: this.form.name };
    if (this.engine === 'custom') {
      body.engine_path = this.form.enginePath;
    }
    if (this.engine !== 'sqlite') {
      body.host = this.form.host;
      body.port = Number.isInteger(this.form.port) ? this.form.port : null;
      body.user = this.form.user;
      body.password = this.form.password;
    }
    let response;
    try {
      response = await wizardFetch('/api/wizard/database/', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    } catch (_) {
      this._abort('Network error. Check the launcher logs and try again.', true);
      return;
    }
    if (response.status === 401 || response.status === 403) {
      expireAndRedirect(
        'Your session expired — please paste the setup token again.',
      );
      return;
    }
    if (response.status === 200) {
      window.location.assign('/admin-user');
      return;
    }
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* tolerate */ }
    if (payload && typeof payload.error === 'string') {
      const msg = ERROR_MESSAGES[payload.error] || ERROR_MESSAGES.generic;
      this._abort(msg, true);
      return;
    }
    this._abort('Could not save database settings.', true);
  },
};

function focusByIndex(idx) {
  const cards = document.querySelectorAll('.db-engine-group [role="radio"]');
  const target = cards[Math.max(0, Math.min(idx, cards.length - 1))];
  if (target) target.focus();
}

function attachKeyboardHandler() {
  const group = document.querySelector('.db-engine-group');
  if (!group) return;
  group.addEventListener('keydown', (event) => {
    const target = event.target.closest('[role="radio"]');
    if (!target) return;
    const cards = Array.from(document.querySelectorAll('.db-engine-group [role="radio"]'));
    const idx = cards.indexOf(target);
    const total = scope.engines.length;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      const next = (idx + 1) % total;
      scope.selectEngine(scope.engines[next].id);
      requestAnimationFrame(() => focusByIndex(next));
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      const prev = (idx - 1 + total) % total;
      scope.selectEngine(scope.engines[prev].id);
      requestAnimationFrame(() => focusByIndex(prev));
    } else if (event.key === 'Home') {
      event.preventDefault();
      scope.selectEngine(scope.engines[0].id);
      requestAnimationFrame(() => focusByIndex(0));
    } else if (event.key === 'End') {
      event.preventDefault();
      scope.selectEngine(scope.engines[total - 1].id);
      requestAnimationFrame(() => focusByIndex(total - 1));
    } else if (event.key === ' ' || event.key === 'Enter') {
      event.preventDefault();
      const choice = scope.engines[idx];
      if (choice) scope.selectEngine(choice.id);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const banner = consumeResumeBanner();
  if (banner) scope.resumeBanner = banner;
  const stash = loadFormState(STEP);
  if (stash) {
    if (typeof stash.engine === 'string') scope.engine = stash.engine;
    if (typeof stash.name === 'string') scope.form.name = stash.name;
    if (typeof stash.host === 'string') scope.form.host = stash.host;
    if (typeof stash.user === 'string') scope.form.user = stash.user;
    if (typeof stash.enginePath === 'string') scope.form.enginePath = stash.enginePath;
  }
  // Port is NOT pulled from the stash — it is always recomputed from
  // the engine here (FE-5). Engine-specific defaults take precedence
  // over any stale value that older sessions might have stashed.
  if (DEFAULT_PORTS[scope.engine] !== undefined) {
    scope.form.port = DEFAULT_PORTS[scope.engine];
  }
  createApp(scope).mount('#app');
  attachKeyboardHandler();
});
