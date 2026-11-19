// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Pure helpers for changing a worker's card choice from the manager
 * (worker card selection FR-CF-5 to FR-CF-8). The form works on a snapshot
 * taken when it opens (R2); the live report only tells it that the choice
 * changed meanwhile or that the worker became read-only.
 */

import {
  CardAlertKind, CardBackend, CardChoiceBody, CardChoiceOutcome, CardChoiceRequest, CardGpuMode,
  CardMethod, CardState, CardStatus,
} from '../../core/services/worker-cards.model';
import { alertsOf, cardName, cardsOf, hasCardStatus, oneFamily } from './worker-cards.util';

export const METHOD_OPTIONS: readonly { value: CardMethod; label: string }[] = [
  { value: 'cpu', label: 'CPU' },
  { value: 'gpu', label: 'GPU' },
  { value: 'cpu_gpu', label: 'CPU and GPU' },
];

/** Alert kinds that hold a card from being newly ticked (D19); `adapter_mismatch` holds nothing. */
const HOLDING_ALERTS: readonly CardAlertKind[] = ['card_lost', 'card_changed', 'card_lost_blender'];
/** States in which a card that is not ticked yet cannot be ticked (FR-CF-5). */
const HELD_STATES: readonly CardState[] = ['changed', 'held', 'blender_lost'];

export const READ_ONLY_ENV = 'The card choice on this worker is set by environment variables.';
export const READ_ONLY_NOT_ALLOWED =
  'This worker does not allow changes from the manager. Turn it on in the worker\'s own settings.';
export const READ_ONLY_NO_STATUS = 'This worker has not reported its graphics cards yet.';

/** One card as the form saw it when it opened. */
export interface SnapshotCard {
  key: string;
  name: string;
  vendor_id: number | null;
  device_id: number | null;
  state: CardState;
  backend: CardBackend | null;
  ticked: boolean;
  screen: boolean | null;
  primary_screen: boolean | null;
}

/** The report as the form saw it when it opened (R2). */
export interface ChoiceSnapshot {
  method: CardMethod;
  gpuMode: CardGpuMode;
  revision: number;
  cards: SnapshotCard[];
  /** Keys named by an open holding alert. */
  holding: string[];
}

function idOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

/** The report's revision, `0` when absent. */
export function revisionOf(status: CardStatus | null | undefined): number {
  return hasCardStatus(status) && typeof status.revision === 'number' ? status.revision : 0;
}

export function includesGpu(method: CardMethod): boolean {
  return method === 'gpu' || method === 'cpu_gpu';
}

export function isMethod(value: string): value is CardMethod {
  return METHOD_OPTIONS.some(o => o.value === value);
}

/** True for a pending request; `null`, `undefined` and `{}` are absent (R3). */
export function hasPending(request: CardChoiceRequest | null | undefined): request is CardChoiceRequest {
  return !!request && typeof request.id === 'string' && request.id !== '';
}

/** True for a reported outcome; `null`, `undefined` and `{}` are absent (R3). */
export function hasOutcome(outcome: CardChoiceOutcome | null | undefined): outcome is CardChoiceOutcome {
  return !!outcome && typeof outcome.id === 'string' && outcome.id !== ''
    && (outcome.result === 'applied' || outcome.result === 'refused');
}

/** The FR-CF-7 sentence when the choice cannot be changed from here, else `null`. */
export function readOnlyReason(status: CardStatus | null | undefined): string | null {
  if (!hasCardStatus(status)) return READ_ONLY_NO_STATUS;
  if (status.env_override === true) return READ_ONLY_ENV;
  if (status.manager_changes_allowed !== true) return READ_ONLY_NOT_ALLOWED;
  return null;
}

/** The snapshot of a report (FR-CF-5). */
export function takeSnapshot(status: CardStatus | null | undefined): ChoiceSnapshot {
  const report = hasCardStatus(status) ? status : null;
  const method = report?.method;
  return {
    method: typeof method === 'string' && isMethod(method) ? method : 'cpu',
    gpuMode: report?.gpu_mode === 'combined' ? 'combined' : 'split',
    revision: revisionOf(report),
    cards: cardsOf(report).map(c => ({
      key: c.key,
      name: cardName(c),
      vendor_id: idOrNull(c.vendor_id),
      device_id: idOrNull(c.device_id),
      state: c.state,
      backend: c.backend ?? null,
      ticked: c.ticked === true,
      screen: c.screen ?? null,
      primary_screen: c.primary_screen ?? null,
    })),
    holding: alertsOf(report).filter(a => HOLDING_ALERTS.includes(a.kind)).map(a => a.key),
  };
}

/** The cards the form lists: every snapshot card that is not `lost`. */
export function shownCards(snapshot: ChoiceSnapshot): SnapshotCard[] {
  return snapshot.cards.filter(c => c.state !== 'lost');
}

/**
 * False for a card that is not ticked yet and is `changed`, `held`,
 * `blender_lost` or named by a holding alert (D19); a ticked card can
 * always stay ticked or be unticked.
 */
export function canNewlyTick(card: SnapshotCard, holding: readonly string[]): boolean {
  if (card.ticked) return true;
  return !HELD_STATES.includes(card.state) && !holding.includes(card.key);
}

/**
 * The POST body, built from the snapshot only: `shown` is the listed keys,
 * `base_revision` the snapshot revision, and `combined` only when the method
 * includes GPU and at least two ticked cards are of one family (R4, FR-CC-4,
 * C-23: the method decides Blender only, as on the worker pages).
 */
export function buildChoiceBody(
  snapshot: ChoiceSnapshot, method: CardMethod, combine: boolean, ticked: ReadonlySet<string>,
): CardChoiceBody {
  const shown = shownCards(snapshot);
  const tickedCards = shown.filter(c => ticked.has(c.key));
  return {
    method,
    gpu_mode: includesGpu(method) && combine && oneFamily(tickedCards) ? 'combined' : 'split',
    cards: tickedCards.map(c => ({ key: c.key, vendor_id: c.vendor_id, device_id: c.device_id })),
    shown: shown.map(c => c.key),
    base_revision: snapshot.revision,
  };
}

/** "Applied<at>" or "Refused<at>: <reason>", where `at` is " at <time>" or ""; shown as text. */
export function outcomeLine(outcome: CardChoiceOutcome, at: string): string {
  if (outcome.result === 'applied') return `Applied${at}`;
  const reason = typeof outcome.reason === 'string' && outcome.reason !== '' ? `: ${outcome.reason}` : '';
  return `Refused${at}${reason}`;
}

/** The pending and outcome ids at the moment a change was withdrawn. */
export interface WithdrawMark {
  requestId: string;
  outcomeId: string | null;
}

/**
 * True when the "Withdrawn" line should end: another request is pending or
 * a new outcome arrived. A late poll that still carries the withdrawn
 * request does not end it.
 */
export function withdrawnEnds(
  mark: WithdrawMark,
  row: { card_choice_request: CardChoiceRequest | null; card_choice_outcome: CardChoiceOutcome | null },
): boolean {
  const request = row.card_choice_request;
  const outcome = row.card_choice_outcome;
  const requestId = hasPending(request) ? request.id : null;
  const outcomeId = hasOutcome(outcome) ? outcome.id : null;
  return (requestId !== null && requestId !== mark.requestId) || outcomeId !== mark.outcomeId;
}

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? '' : 's'}`;
}

/** How long ago `since` was, in words ("less than a minute", "3 minutes", "2 days"). */
export function ageText(since: string, now: number): string {
  const start = Date.parse(since);
  if (Number.isNaN(start)) return 'some time';
  const minutes = Math.floor(Math.max(0, now - start) / 60000);
  if (minutes < 1) return 'less than a minute';
  if (minutes < 60) return plural(minutes, 'minute');
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? plural(hours, 'hour') : plural(Math.floor(hours / 24), 'day');
}
