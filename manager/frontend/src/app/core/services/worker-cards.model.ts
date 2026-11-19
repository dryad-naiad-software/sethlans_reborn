// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Types of a worker's graphics card report and of the card actions
 * (worker card selection FR-CF-1). The report is self-reported by the
 * worker and checked by the manager (shapes in
 * `manager/workers/serializers/worker_card_schemas.py`); every text in it
 * is shown through Angular interpolation only.
 */

export type CardMethod = 'cpu' | 'gpu' | 'cpu_gpu';
export type CardGpuMode = 'split' | 'combined';
export type CardState =
  'ok' | 'lost' | 'changed' | 'blender_lost' | 'held' | 'unticked' | 'unknown';
export type CardBackend = 'CUDA' | 'OPTIX' | 'HIP' | 'ONEAPI' | 'METAL';
export type CardAlertKind =
  'card_lost' | 'card_changed' | 'card_lost_blender' | 'adapter_mismatch';
export type CardNoticeKind = 'card_new' | 'card_gone';
export type CardOutcomeCode =
  'applied' | 'invalid' | 'env_override' | 'not_allowed' | 'probe_failed'
  | 'identity_mismatch' | 'stale_revision' | 'card_held' | 'save_failed';

/** One card of the report. The manager drops entries without `key` or `state`. */
export interface CardEntry {
  key: string;
  state: CardState;
  name?: string;
  vendor_id?: number | null;
  device_id?: number | null;
  ticked?: boolean;
  blender?: boolean;
  backend?: CardBackend | null;
  note?: string;
  screen?: boolean | null;
  primary_screen?: boolean | null;
}

/** An open alert. The manager drops entries without `id`, `kind`, `key` or `raised_at`. */
export interface CardAlert {
  id: string;
  kind: CardAlertKind;
  key: string;
  raised_at: string | null;
  name?: string;
  detail?: string;
}

export interface CardNotice {
  kind: CardNoticeKind;
  key: string;
  name?: string;
}

export interface CardWarning {
  code: string;
  text: string;
  key?: string | null;
}

/** The stored report; `{}` and `null` both mean "not reported" (see `hasCardStatus`). */
export interface CardStatus {
  schema?: number;
  method?: CardMethod;
  gpu_mode?: CardGpuMode;
  env_override?: boolean;
  revision?: number;
  manager_changes_allowed?: boolean;
  rebuild_waiting?: boolean;
  problem?: string;
  cards?: CardEntry[];
  alerts?: CardAlert[];
  notices?: CardNotice[];
  warnings?: CardWarning[];
}

/** A ticked card named in a change request. */
export interface CardChoiceCard {
  key: string;
  vendor_id: number | null;
  device_id: number | null;
}

/** Body of `POST /api/heartbeat/{pk}/card-choice/`. */
export interface CardChoiceBody {
  method: CardMethod;
  gpu_mode: CardGpuMode;
  cards: CardChoiceCard[];
  shown: string[];
  base_revision: number;
}

/** The pending change request stored on the worker row. */
export interface CardChoiceRequest extends CardChoiceBody {
  id: string;
  requested_by: string;
  requested_at: string;
}

/** The last outcome the worker reported for a change request. */
export interface CardChoiceOutcome {
  id: string;
  result: 'applied' | 'refused';
  code: CardOutcomeCode;
  reason: string;
  revision: number;
  finished_at: string;
  requested_by?: string;
  requested_at?: string;
}

/** 202 answer of the card-choice POST. */
export interface CardChoiceSendResponse {
  card_choice_request: CardChoiceRequest;
  replaced: string | null;
}

/** 200 answer of `POST /api/heartbeat/{pk}/dismiss-card-alert/`. */
export interface DismissCardAlertResponse {
  card_status: CardStatus | null;
  card_alert_dismissals: string[];
}

/** Every non-2xx answer of the card actions. */
export interface CardActionError {
  detail?: string;
}
