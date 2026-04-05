// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ShutdownService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBaseUrl;

  shutdown(): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(
      `${this.apiBase}/system/shutdown/`,
      {},
    );
  }
}
