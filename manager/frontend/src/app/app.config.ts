// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  ApplicationConfig,
  provideZoneChangeDetection,
  APP_INITIALIZER,
  DOCUMENT,
  inject,
} from '@angular/core';
import { LocationStrategy } from '@angular/common';
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
import { SetupPathLocationStrategy } from './features/setup/setup-location-strategy';

/**
 * Primes the CSRF cookie at app start so the first mutating request has
 * a valid `csrftoken` cookie to copy into `X-CSRFToken`. Errors are
 * swallowed — the resolver always resolves so app bootstrap is never
 * blocked by a transient CSRF fetch failure.
 *
 * Skipped on the setup page (`/setup/`): setup mode has no runtime API.
 */
export function primeCsrfCookie(): () => Promise<void> {
  const authService = inject(AuthService);
  const path = inject(DOCUMENT).location.pathname;
  return () =>
    new Promise<void>((resolve) => {
      if (isSetupPath(path)) {
        resolve();
        return;
      }
      authService.fetchCsrfToken().subscribe({
        next: () => resolve(),
        error: () => resolve(),
      });
    });
}

/** True for the setup page's path, `/setup` or `/setup/...`. */
export function isSetupPath(path: string): boolean {
  return path === '/setup' || path.startsWith('/setup/');
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    { provide: LocationStrategy, useClass: SetupPathLocationStrategy },
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
