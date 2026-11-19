// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/** The worker list's row values, computed once per poll or applied action. */

import { Worker } from '../../core/services/worker.service';
import { alertCount, gpuColumnText } from './worker-cards.util';
import { hasPending } from './worker-card-choice.util';

export interface WorkerRow extends Worker {
  /** `ui_url` when it is an http(s) URL, else `null`. */
  uiUrl: string | null;
  alertCount: number;
  gpuText: string;
  /** A change request waits for the worker (FR-CF-2 "Change pending" chip). */
  pending: boolean;
}

/** Fields a card action's answer replaces on its row at once (FR-CF-6). */
export type WorkerCardPatch =
  Partial<Pick<Worker, 'card_status' | 'card_alert_dismissals' | 'card_choice_request'>>;

export function toRow(w: Worker): WorkerRow {
  return {
    ...w,
    uiUrl: httpUrl(w.ui_url),
    alertCount: alertCount(w.card_status),
    gpuText: gpuColumnText(w.card_status, w.gpu_name),
    pending: hasPending(w.card_choice_request),
  };
}

/** The URL when it parses with an http: or https: scheme, else `null`. */
export function httpUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? url : null;
  } catch {
    return null;
  }
}
