// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Shared wizard chrome — stepper data + Back-nav helpers (issue #179).
//
// Phase 2 shipped each step as an isolated card with only a Continue
// button. The wizard now has a proper container with a horizontal
// stepper in the card header and Back/Continue navigation in the
// footer. This module owns the data and behaviour shared across every
// post-Welcome page so each per-step controller can opt in by spreading
// the helpers into its Petite-vue scope.
//
// Stepper rules (locked in issue #179):
//   - 7 entries for `manager` topology:
//       Topology, Network, Database, Admin, FFmpeg, Verify, Done.
//   - 8 entries for `manager_worker`: Worker Password between Admin
//     and FFmpeg.
//   - Welcome is pre-stepper (no entry).
//   - Visual states: current = brand fill + bold; completed = brand
//     fill + normal weight; future = muted gray.
//   - Non-interactive forward navigation. The stepper is a status
//     indicator only — Back / Continue are the only navigation
//     primitives.
//
// Topology is read from /api/wizard/resume-target/ once on page mount
// (no per-page network duplication of the topology probe). The page's
// chrome falls back to `manager` topology when the probe is unavailable
// — that keeps the stepper short rather than visually promising a
// step that does not exist for the current flow.

import { wizardFetch } from '/static/js/common.js';
import {
  ICON_TOPOLOGY,
  ICON_NETWORK,
  ICON_DATABASE,
  ICON_PERSON,
  ICON_KEY,
  ICON_FILM,
  ICON_CHECK,
  ICON_FLAG,
} from '/static/js/wizard_chrome_icons.js';

// Step identifier constants — page controllers use these to declare
// which slot they occupy in the stepper.
export const STEP_TOPOLOGY = 'topology';
export const STEP_NETWORK = 'network';
export const STEP_DATABASE = 'database';
export const STEP_ADMIN = 'admin';
export const STEP_WORKER = 'worker';
export const STEP_FFMPEG = 'ffmpeg';
export const STEP_VERIFY = 'verify';
export const STEP_DONE = 'done';

// Canonical step definitions (id, label, icon). The page chrome filters
// this list by topology to produce the active stepper.
const STEP_DEFS = Object.freeze([
  { id: STEP_TOPOLOGY, label: 'Topology', icon: ICON_TOPOLOGY, route: '/topology' },
  { id: STEP_NETWORK,  label: 'Network',  icon: ICON_NETWORK,  route: '/network' },
  { id: STEP_DATABASE, label: 'Database', icon: ICON_DATABASE, route: '/database' },
  { id: STEP_ADMIN,    label: 'Admin',    icon: ICON_PERSON,   route: '/admin-user' },
  { id: STEP_WORKER,   label: 'Worker',   icon: ICON_KEY,      route: '/worker-password' },
  { id: STEP_FFMPEG,   label: 'FFmpeg',   icon: ICON_FILM,     route: '/ffmpeg' },
  { id: STEP_VERIFY,   label: 'Verify',   icon: ICON_CHECK,    route: '/verify' },
  { id: STEP_DONE,     label: 'Done',     icon: ICON_FLAG,     route: '/done' },
]);

// FR-CHK3 checkpoint name → step id (for derived step states).
const CHECKPOINT_TO_STEP = Object.freeze({
  topology_chosen:     STEP_TOPOLOGY,
  network_configured:  STEP_NETWORK,
  database_configured: STEP_DATABASE,
  admin_validated:     STEP_ADMIN,
  worker_password_set: STEP_WORKER,
  ffmpeg_installed:    STEP_FFMPEG,
  verified:            STEP_VERIFY,
});

// Topology-aware step list. `manager` topology drops the Worker step.
// Any unrecognised topology defaults to manager (the more conservative
// option — fewer dots in the indicator) until /resume-target reports
// otherwise.
export function stepsForTopology(topology) {
  const skipWorker = (topology !== 'manager_worker');
  return STEP_DEFS.filter(s => !(skipWorker && s.id === STEP_WORKER));
}

// Compute the {steps, currentIndex} for rendering.
//
// `completed` accepts either a list of FR-CHK3 checkpoint names
// (e.g. ['topology_chosen', 'network_configured']) or a Set of
// step-ids (e.g. new Set(['topology', 'network'])). Checkpoint names
// are translated automatically — most callers find the checkpoint
// form more natural because it mirrors the names already used by
// progress.json and the backend handlers.
//
// `currentId` is the step-id the user is on right now.
export function buildStepperModel(topology, currentId, completed) {
  const steps = stepsForTopology(topology);
  let completedStepIds;
  if (completed instanceof Set) {
    completedStepIds = completed;
  } else if (Array.isArray(completed)) {
    // Translate any checkpoint names to step ids; keep step ids as-is
    // so callers that already pre-translated remain valid.
    completedStepIds = new Set();
    for (const name of completed) {
      if (CHECKPOINT_TO_STEP[name]) {
        completedStepIds.add(CHECKPOINT_TO_STEP[name]);
      } else {
        completedStepIds.add(name);
      }
    }
  } else {
    completedStepIds = new Set();
  }
  const out = steps.map((s, idx) => ({
    id: s.id,
    label: s.label,
    icon: s.icon,
    route: s.route,
    isCurrent: s.id === currentId,
    isCompleted: completedStepIds.has(s.id),
    index: idx,
  }));
  return out;
}

// Convert a checkpoint list (from /resume-target or progress.json) into
// the Set<stepId> the stepper expects. Tolerates unknown checkpoint
// names (silently dropped).
export function checkpointsToStepIds(checkpoints) {
  const out = new Set();
  if (!Array.isArray(checkpoints)) return out;
  for (const name of checkpoints) {
    const sid = CHECKPOINT_TO_STEP[name];
    if (sid) out.add(sid);
  }
  return out;
}

// Probe /api/wizard/resume-target/ once and return {topology, checkpoints}.
// Returns sensible defaults on any failure so the chrome still renders.
export async function fetchChromeContext() {
  let topology = null;
  const checkpoints = [];
  try {
    const r = await wizardFetch('/api/wizard/resume-target/');
    if (r && r.ok) {
      const body = await r.json();
      if (body && typeof body.topology === 'string') {
        topology = body.topology;
      }
      // resume-target does not return checkpoints, but a future bump
      // could add them. Keep the call shape forward-compatible.
      if (body && Array.isArray(body.checkpoints)) {
        for (const n of body.checkpoints) {
          if (typeof n === 'string') checkpoints.push(n);
        }
      }
    }
  } catch (_) { /* tolerated; defaults apply */ }
  return { topology, checkpoints };
}

// Apply chrome data to a reactive scope after Petite-vue has mounted.
// The scope is expected to expose `stepper`, `topology`, and a
// `goBack` method bound to the current step. Calling this after
// mount via `scope.applyChromeContext(currentStep, knownCheckpoints)`
// re-renders the stepper with the topology-aware step list.
//
// `knownCheckpoints` is the list the calling page synthesises from
// its own position — every page knows which checkpoints MUST have
// been recorded for it to be reachable (e.g., the database page is
// only reachable after topology_chosen + network_configured). Passing
// these in keeps the stepper's "completed" trail accurate without
// requiring a second backend round-trip — the resume-target endpoint
// only reports topology, not checkpoints.
//
// We export the procedure as a free function rather than a mixin so
// the per-page controllers stay explicit about what's happening — no
// hidden lifecycle hook surprises.
export async function applyChromeContext(scope, currentStepId, knownCheckpoints) {
  const ctx = await fetchChromeContext();
  scope.topology = ctx.topology;
  scope.stepper = buildStepperModel(
    ctx.topology,
    currentStepId,
    Array.isArray(knownCheckpoints) ? knownCheckpoints : ctx.checkpoints,
  );
}

// Page-by-page Back navigation map. Welcome → no Back. Topology is the
// first stepper entry → no Back. The Done page has no Back once the
// pending-setup POST has fired (handled by the done page itself; this
// map only covers the navigable post-Welcome pages).
//
// Worker-password is special: when topology is `manager` the page
// auto-forwards to /ffmpeg on mount, so the FFmpeg page's "Back"
// destination depends on topology. The Back button on /ffmpeg routes
// to /admin-user for `manager` and to /worker-password otherwise.
export function backRouteFor(currentStepId, topology) {
  switch (currentStepId) {
    case STEP_NETWORK:  return '/topology';
    case STEP_DATABASE: return '/network';
    case STEP_ADMIN:    return '/database';
    case STEP_WORKER:   return '/admin-user';
    case STEP_FFMPEG:
      return (topology === 'manager_worker') ? '/worker-password' : '/admin-user';
    case STEP_VERIFY:   return '/ffmpeg';
    default:            return null;
  }
}
