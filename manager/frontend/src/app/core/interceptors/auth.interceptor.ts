// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { MatSnackBar } from '@angular/material/snack-bar';
import { catchError, throwError } from 'rxjs';
import { AuthService } from '../services/auth.service';

function getCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp('(?:^|;\\s*)' + name + '=([^;]*)')
  );
  return match ? decodeURIComponent(match[1]) : null;
}

const MUTATING_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE'];

/**
 * Attaches CSRF token and withCredentials to requests.
 * Handles 401 (redirect to login) and 403 (snackbar notification).
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
      if (error.status === 401 && !isLoginRequest) {
        authService.setUnauthenticated();
        router.navigate(['/login']);
      }
      if (error.status === 403) {
        snackBar.open('Permission denied', 'Dismiss', { duration: 5000 });
      }
      if (error.status === 503 && !req.url.includes('/api/setup/')) {
        const body = error.error;
        if (body?.detail === 'Setup not complete.') {
          router.navigate(['/setup']);
        }
      }
      return throwError(() => error);
    })
  );
};
