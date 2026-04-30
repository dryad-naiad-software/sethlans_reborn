// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Per-page form-state stash for the wizard frontend (FR-CHK3-FORM-STATE).
//
// Stored in sessionStorage (NOT localStorage — sessionStorage clears on
// tab close so it never persists credentials beyond the wizard's
// short lifetime). One key per step, e.g. ``wizard.form.network``.
//
// Password fields are NEVER stashed — the admin password, password
// confirmation, worker UI password, and database password MUST re-prompt
// the user on every visit. Callers pass a `fields` object that omits
// password fields entirely.
//
// Stash is cleared on:
//   (a) successful submission of FFmpeg or Verify steps (irreversible
//       installer boundaries);
//   (b) a re-auth event (FR-CHK3-RESUME) — the user re-enters values
//       uniformly across all steps.

const STEP_KEY_PREFIX = 'wizard.form.';

// Known step names — used by clearAllFormState. Adding a new step? add
// the name here too so the all-clear sweep covers it. Only steps whose
// page actually calls stashFormState appear here. 'welcome' and 'done'
// were removed because neither page stashes any user input (FE-6).
export const FORM_STATE_STEPS = Object.freeze([
  'topology',
  'network',
  'database',
  'admin-user',
  'worker-password',
  'verify',
]);

function _key(step) {
  return STEP_KEY_PREFIX + step;
}

export function stashFormState(step, fields) {
  if (typeof step !== 'string' || !step) return;
  if (fields === null || typeof fields !== 'object') return;
  let serialized;
  try { serialized = JSON.stringify(fields); }
  catch (_) { return; }
  try { window.sessionStorage.setItem(_key(step), serialized); }
  catch (_) { /* sessionStorage may be disabled */ }
}

export function loadFormState(step) {
  if (typeof step !== 'string' || !step) return null;
  let raw;
  try { raw = window.sessionStorage.getItem(_key(step)); }
  catch (_) { return null; }
  if (raw === null) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') return parsed;
    return null;
  } catch (_) {
    return null;
  }
}

export function clearFormState(step) {
  if (typeof step !== 'string' || !step) return;
  try { window.sessionStorage.removeItem(_key(step)); }
  catch (_) { /* sessionStorage may be disabled */ }
}

export function clearAllFormState() {
  for (const step of FORM_STATE_STEPS) {
    clearFormState(step);
  }
}
