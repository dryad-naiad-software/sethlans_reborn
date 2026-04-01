// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';

export interface Job {
  id: number;
  project: number;
  status: string;
  render_engine: string;
  render_device: string;
  cycles_feature_set: string;
  frame_number: number;
  tile_x: number;
  tile_y: number;
  output_file: string;
  worker: number | null;
  created_at: string;
  updated_at: string;
}

export interface JobFilter {
  status?: string;
  project?: number;
  worker?: number;
}

@Injectable({ providedIn: 'root' })
export class JobService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/jobs`;

  list(filters?: JobFilter): Observable<Job[]> {
    let params = new HttpParams();
    if (filters?.status) {
      params = params.set('status', filters.status);
    }
    if (filters?.project) {
      params = params.set('project', filters.project.toString());
    }
    if (filters?.worker) {
      params = params.set('worker', filters.worker.toString());
    }
    return this.http.get<Job[]>(`${this.baseUrl}/`, { params });
  }

  pollList(filters?: JobFilter): Observable<Job[]> {
    return poll(() => this.list(filters));
  }

  get(id: number): Observable<Job> {
    return this.http.get<Job>(`${this.baseUrl}/${id}/`);
  }

  pollDetail(id: number): Observable<Job> {
    return poll(() => this.get(id));
  }
}
