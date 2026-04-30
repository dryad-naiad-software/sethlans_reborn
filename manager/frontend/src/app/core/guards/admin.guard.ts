// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, map, of } from 'rxjs';
import { AuthService } from '../services/auth.service';

/**
 * Admin guard — allows route access only to authenticated users with `is_staff === true`.
 *
 * Behavior:
 *   - If `authService.user?.is_staff` is true, allow.
 *   - If a user is loaded but not staff, redirect to `/`.
 *   - If no user is loaded yet, attempt `getCurrentUser()` and recheck.
 *   - On auth failure (401), redirect to `/login` (mirroring `authGuard`).
 */
export const adminGuard: CanActivateFn = () => {
  const authService = inject(AuthService);
  const router = inject(Router);

  const currentUser = authService.user;
  if (currentUser) {
    if (currentUser.is_staff) {
      return true;
    }
    router.navigate(['/']);
    return false;
  }

  return authService.getCurrentUser().pipe(
    map(user => {
      if (user.is_staff) {
        return true;
      }
      router.navigate(['/']);
      return false;
    }),
    catchError(() => {
      router.navigate(['/login']);
      return of(false);
    }),
  );
};
