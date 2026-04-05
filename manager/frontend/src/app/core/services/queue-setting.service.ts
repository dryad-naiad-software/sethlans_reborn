// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';

export interface QueueStatus {
  queue_paused: boolean;
}

@Injectable({ providedIn: 'root' })
export class QueueSettingService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/queue-settings`;

  getStatus(): Observable<QueueStatus> {
    return this.http.get<QueueStatus>(`${this.baseUrl}/`);
  }

  pollStatus(): Observable<QueueStatus> {
    return poll(() => this.getStatus());
  }

  pause(): Observable<QueueStatus> {
    return this.http.post<QueueStatus>(`${this.baseUrl}/pause/`, {});
  }

  resume(): Observable<QueueStatus> {
    return this.http.post<QueueStatus>(`${this.baseUrl}/resume/`, {});
  }
}
