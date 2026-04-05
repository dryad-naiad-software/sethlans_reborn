// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';
import { SupportedVersion } from './supported-version.service';

export interface Project {
  id: string;
  name: string;
  blender_version: number;
  blender_version_details: SupportedVersion;
  created_at: string;
  is_paused: boolean;
}

@Injectable({ providedIn: 'root' })
export class ProjectService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/projects`;

  list(): Observable<Project[]> {
    return this.http.get<Project[]>(`${this.baseUrl}/`);
  }

  pollList(): Observable<Project[]> {
    return poll(() => this.list());
  }

  get(id: string): Observable<Project> {
    return this.http.get<Project>(`${this.baseUrl}/${id}/`);
  }

  pollDetail(id: string): Observable<Project> {
    return poll(() => this.get(id));
  }

  create(data: { name: string; blender_version: number }): Observable<Project> {
    return this.http.post<Project>(`${this.baseUrl}/`, data);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}/`);
  }

  pause(id: string): Observable<Project> {
    return this.http.post<Project>(`${this.baseUrl}/${id}/pause/`, {});
  }

  unpause(id: string): Observable<Project> {
    return this.http.post<Project>(`${this.baseUrl}/${id}/unpause/`, {});
  }
}
