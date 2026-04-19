// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import { environment } from '../../../../environments/environment';

/**
 * Isolates the only anonymous API call (POST /api/setup/bootstrap/) from
 * SetupApiService. Kept separate so APP_INITIALIZER has a shallow dependency
 * graph and cannot form a cycle with SetupStateService.
 */
@Injectable({ providedIn: 'root' })
export class SetupBootstrapService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiBaseUrl}/setup/bootstrap/`;

  bootstrap(token: string): Observable<void> {
    return this.http
      .post(this.url, { token }, { withCredentials: true, observe: 'response' })
      .pipe(map(() => void 0));
  }
}
