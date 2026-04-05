// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';

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
}

@Injectable({ providedIn: 'root' })
export class WorkerService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/heartbeat`;

  list(): Observable<Worker[]> {
    return this.http.get<Worker[]>(`${this.baseUrl}/`);
  }

  pollList(): Observable<Worker[]> {
    return poll(() => this.list());
  }

  get(id: number): Observable<Worker> {
    return this.http.get<Worker>(`${this.baseUrl}/${id}/`);
  }
}
