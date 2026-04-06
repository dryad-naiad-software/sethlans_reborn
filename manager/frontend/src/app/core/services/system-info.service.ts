// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, of, tap } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface SystemInfo {
  ffmpeg_available: boolean;
}

@Injectable({ providedIn: 'root' })
export class SystemInfoService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/system-info`;
  private readonly cache$ = new BehaviorSubject<SystemInfo | null>(null);
  private fetched = false;

  getSystemInfo(): Observable<SystemInfo> {
    const cached = this.cache$.value;
    if (cached !== null && this.fetched) {
      return of(cached);
    }
    return this.http.get<SystemInfo>(`${this.baseUrl}/`).pipe(
      tap(info => {
        this.cache$.next(info);
        this.fetched = true;
      }),
    );
  }
}
