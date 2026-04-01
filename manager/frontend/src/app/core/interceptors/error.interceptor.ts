// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { HttpInterceptorFn } from '@angular/common/http';
import { catchError, throwError } from 'rxjs';

/**
 * Global error interceptor that logs HTTP errors.
 * Can be extended to show toast notifications or redirect on 401.
 */
export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  return next(req).pipe(
    catchError((error) => {
      console.error(`HTTP Error ${error.status}: ${req.method} ${req.url}`, error);
      return throwError(() => error);
    })
  );
};
