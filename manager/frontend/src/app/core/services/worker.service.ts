// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll, PollErrorHandler } from './polling.util';
import {
  CardChoiceOutcome, CardChoiceRequest, CardStatus, DismissCardAlertResponse,
} from './worker-cards.model';

export interface Worker {
  id: number;
  hostname: string;
  ip_address: string;
  status: string;
  cpu_name: string;
  gpu_name: string;
  os: string;
  ui_url: string | null;
  last_heartbeat: string;
  is_active: boolean;
  has_token: boolean;
  /** Self-reported graphics card report; `null` until the worker sent one. */
  card_status: CardStatus | null;
  /** Alert ids an admin dismissed that the worker still reports. */
  card_alert_dismissals: string[];
  /** Pending change request from the manager (not sent by the API yet). */
  card_choice_request?: CardChoiceRequest | null;
  /** Last outcome of a change request (not sent by the API yet). */
  card_choice_outcome?: CardChoiceOutcome | null;
}

@Injectable({ providedIn: 'root' })
export class WorkerService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/heartbeat`;

  list(): Observable<Worker[]> {
    return this.http.get<Worker[]>(`${this.baseUrl}/`);
  }

  pollList(onError?: PollErrorHandler): Observable<Worker[]> {
    return poll(() => this.list(), onError);
  }

  get(id: number): Observable<Worker> {
    return this.http.get<Worker>(`${this.baseUrl}/${id}/`);
  }

  /**
   * Dismisses one card alert. The URL carries only the worker's numeric id;
   * the alert id goes in the body.
   */
  dismissCardAlert(workerId: number, alertId: string): Observable<DismissCardAlertResponse> {
    return this.http.post<DismissCardAlertResponse>(
      `${this.baseUrl}/${workerId}/dismiss-card-alert/`, { alert_id: alertId },
    );
  }
}
