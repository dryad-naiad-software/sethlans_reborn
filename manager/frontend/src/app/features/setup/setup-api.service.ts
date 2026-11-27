// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import {
  HttpBackend,
  HttpClient,
  HttpErrorResponse,
  HttpHeaders,
} from '@angular/common/http';
import { Observable, catchError, map, throwError } from 'rxjs';
import {
  FieldErrors,
  FinishInfo,
  SetupAnswers,
  SetupFailure,
  SetupForm,
  SetupHealth,
} from './setup.types';

const SESSION_HEADER = 'X-Setup-Session';

/**
 * The setup-mode API (FR-SM-3). It uses its own `HttpClient` on
 * `HttpBackend`, so the running app's interceptors (CSRF, the 401 redirect
 * to /login, the 403 "Permission denied" snackbar) never touch it: setup
 * mode has no login and answers 403 for a wrong token or a lost session.
 *
 * The session token lives in memory only; a reload asks for the setup
 * token again (a new session replaces the old one on the server).
 */
@Injectable({ providedIn: 'root' })
export class SetupApiService {
  private readonly http = new HttpClient(inject(HttpBackend));
  private session = '';

  get hasSession(): boolean {
    return this.session !== '';
  }

  clearSession(): void {
    this.session = '';
  }

  health(): Observable<SetupHealth> {
    return this.http.get<SetupHealth>('/api/health/').pipe(catchError(toFailure));
  }

  auth(token: string): Observable<void> {
    return this.http
      .post<{ session_token: string }>('/api/setup/auth/', { token })
      .pipe(
        map(body => {
          this.session = body.session_token;
        }),
        catchError(toFailure),
      );
  }

  form(): Observable<SetupForm> {
    return this.http
      .get<SetupForm>('/api/setup/form/', { headers: this.headers() })
      .pipe(catchError(toFailure));
  }

  /** Sends only the form's answer parts (`answersOf`). */
  submit(form: SetupForm): Observable<FinishInfo> {
    return this.http
      .post<FinishInfo>('/api/setup/submit/', answersOf(form), { headers: this.headers() })
      .pipe(catchError(toFailure));
  }

  private headers(): HttpHeaders {
    return new HttpHeaders({ [SESSION_HEADER]: this.session });
  }
}

/**
 * The answer parts of the form, copied: never the notices, last failure,
 * cards, managers, leftovers or other read-only parts. The typed manager
 * address is trimmed here, not while typing.
 */
export function answersOf(form: SetupForm): SetupAnswers {
  return {
    mode: form.mode,
    server: { ...form.server },
    data_folder: { path: form.data_folder.path },
    database: { ...form.database },
    admin: { ...form.admin },
    worker: { ...form.worker, cards: [...form.worker.cards] },
    manager: { ...form.manager, url: form.manager.url.trim() },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The text lists under `body[key]` by dotted key (`fields`, `codes`). */
function listsOf(body: Record<string, unknown>, key: string): FieldErrors {
  const raw = body[key];
  const fields: FieldErrors = {};
  if (!isRecord(raw)) {
    return fields;
  }
  for (const [key, value] of Object.entries(raw)) {
    if (Array.isArray(value)) {
      fields[key] = value.filter((item): item is string => typeof item === 'string');
    }
  }
  return fields;
}

/** Any error answer as a `SetupFailure` (every setup-mode error body has `error`). */
export function toFailure(err: unknown): Observable<never> {
  const response = err instanceof HttpErrorResponse ? err : null;
  const status = response?.status ?? 0;
  const body = isRecord(response?.error) ? response.error : {};
  const code = typeof body['error'] === 'string' ? body['error'] : '';
  let message = typeof body['message'] === 'string' ? body['message'] : '';
  if (!message) {
    message = status === 0
      ? 'The setup server could not be reached.'
      : `The setup server answered ${status}.`;
  }
  const failure: SetupFailure = {
    status, code, message, fields: listsOf(body, 'fields'), codes: listsOf(body, 'codes'),
  };
  return throwError(() => failure);
}
