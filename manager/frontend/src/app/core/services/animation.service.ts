// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';

export interface Animation {
  id: number;
  project: number;
  name: string;
  start_frame: number;
  end_frame: number;
  status: string;
  progress: number;
  created_at: string;
  updated_at: string;
}

export interface CreateAnimationRequest {
  project: number;
  start_frame: number;
  end_frame: number;
  render_engine?: string;
  render_device?: string;
  tiling_enabled?: boolean;
  tiling_configuration?: string;
}

@Injectable({ providedIn: 'root' })
export class AnimationService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/animations`;

  list(): Observable<Animation[]> {
    return this.http.get<Animation[]>(`${this.baseUrl}/`);
  }

  pollList(): Observable<Animation[]> {
    return poll(() => this.list());
  }

  get(id: number): Observable<Animation> {
    return this.http.get<Animation>(`${this.baseUrl}/${id}/`);
  }

  pollDetail(id: number): Observable<Animation> {
    return poll(() => this.get(id));
  }

  create(data: CreateAnimationRequest): Observable<Animation> {
    return this.http.post<Animation>(`${this.baseUrl}/`, data);
  }
}
