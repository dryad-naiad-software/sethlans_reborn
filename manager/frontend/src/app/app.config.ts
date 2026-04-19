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
 * Primes the CSRF cookie before the first mutating request. The setup
 * bootstrap POST itself is `@csrf_exempt` on the server, but every
 * downstream wizard mutation still requires a CSRF token — so the cookie
 * must be populated at app start. Must run whether or not setup is in
 * progress (errors are swallowed).
 */
export function initializeSetupCheck(): () => Promise<void> {
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
      useFactory: initializeSetupCheck,
      multi: true,
    },
  ],
};
