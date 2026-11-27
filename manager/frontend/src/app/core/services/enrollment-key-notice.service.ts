// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { components } from '../api-types';
import { environment } from '../../../environments/environment';

/** `{show, enrollment_key}`; the key (hyphenated display form) only while `show`. */
export type EnrollmentKeyNotice = components['schemas']['EnrollmentKeyNotice'];

/**
 * The dashboard's one-time enrollment key notice (setup-rebuild FR-WZ-9),
 * `/api/auth/enrollment_key_notice/`. Admin only: the server answers 403
 * to anyone else, so callers ask only for staff users.
 */
@Injectable({ providedIn: 'root' })
export class EnrollmentKeyNoticeService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiBaseUrl}/auth/enrollment_key_notice/`;

  get(): Observable<EnrollmentKeyNotice> {
    return this.http.get<EnrollmentKeyNotice>(this.url);
  }

  /** Hides the notice for good; answers `{show: false, enrollment_key: null}`. */
  dismiss(): Observable<EnrollmentKeyNotice> {
    return this.http.post<EnrollmentKeyNotice>(this.url, null);
  }
}
