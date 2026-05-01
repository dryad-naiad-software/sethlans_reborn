// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  ApplicationConfig,
  provideZoneChangeDetection,
  APP_INITIALIZER,
  inject,
} from '@angular/core';
import { provideRouter } from '@angular/router';
import {
  provideHttpClient,
  withInterceptors,
  withXsrfConfiguration,
} from '@angular/common/http';
import { provideAnimations } from '@angular/platform-browser/animations';

import { routes } from './app.routes';
import { errorInterceptor } from './core/interceptors/error.interceptor';
import { authInterceptor } from './core/interceptors/auth.interceptor';
import { AuthService } from './core/services/auth.service';

/**
 * Primes the CSRF cookie at app start so the first mutating request has
 * a valid `csrftoken` cookie to copy into `X-CSRFToken`. Errors are
 * swallowed — the resolver always resolves so app bootstrap is never
 * blocked by a transient CSRF fetch failure.
 */
export function primeCsrfCookie(): () => Promise<void> {
  const authService = inject(AuthService);
  return () =>
    new Promise<void>((resolve) => {
      authService.fetchCsrfToken().subscribe({
        next: () => resolve(),
        error: () => resolve(),
      });
    });
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideHttpClient(
      withInterceptors([authInterceptor, errorInterceptor]),
      withXsrfConfiguration({
        cookieName: 'csrftoken',
        headerName: 'X-CSRFToken',
      }),
    ),
    provideAnimations(),
    {
      provide: APP_INITIALIZER,
      useFactory: primeCsrfCookie,
      multi: true,
    },
  ],
};
