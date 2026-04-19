// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { MatSnackBar } from '@angular/material/snack-bar';
import { catchError, throwError } from 'rxjs';
import { AuthService } from '../services/auth.service';
import { SetupErrorEnvelope, SetupErrorCode } from '../models/error-envelope';

function getCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp('(?:^|;\\s*)' + name + '=([^;]*)')
  );
  return match ? decodeURIComponent(match[1]) : null;
}

const MUTATING_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE'];

function extractSetupErrorCode(error: HttpErrorResponse): SetupErrorCode | null {
  const envelope = error.error as SetupErrorEnvelope | undefined;
  return envelope?.error?.code ?? null;
}

/**
 * Attaches CSRF token and withCredentials to requests.
 * Handles 401 (redirect to login), 403 (snackbar), and setup-envelope error
 * codes (setup_in_progress → /setup, setup_complete/setup_session_conflict →
 * /login). invalid_token is handled inline by TokenEntryComponent, not here.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const authService = inject(AuthService);
  const router = inject(Router);
  const snackBar = inject(MatSnackBar);

  let modifiedReq = req.clone({ withCredentials: true });

  if (MUTATING_METHODS.includes(req.method)) {
    const csrfToken = getCookie('csrftoken');
    if (csrfToken) {
      modifiedReq = modifiedReq.clone({
        setHeaders: { 'X-CSRFToken': csrfToken },
      });
    }
  }

  const isLoginRequest = req.url.includes('/auth/login');

  return next(modifiedReq).pipe(
    catchError((error: HttpErrorResponse) => {
      const code = extractSetupErrorCode(error);
      const currentPath = router.url.split('?')[0];

      if (code === 'setup_in_progress') {
        if (!currentPath.startsWith('/setup')) {
          router.navigate(['/setup'], { queryParamsHandling: 'preserve' });
        }
        return throwError(() => error);
      }

      if (code === 'setup_complete' || code === 'setup_session_conflict') {
        router.navigate(['/login']);
        return throwError(() => error);
      }

      if (code === 'invalid_token') {
        // TokenEntryComponent renders this inline. No-op here.
        return throwError(() => error);
      }

      if (error.status === 401 && !isLoginRequest) {
        authService.setUnauthenticated();
        router.navigate(['/login']);
      }
      if (error.status === 403 && !code) {
        snackBar.open('Permission denied', 'Dismiss', { duration: 5000 });
      }
      return throwError(() => error);
    })
  );
};
