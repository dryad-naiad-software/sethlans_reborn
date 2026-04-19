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
  HttpErrorResponse,
  provideHttpClient,
  withInterceptors,
  withXsrfConfiguration,
} from '@angular/common/http';
import { provideAnimations } from '@angular/platform-browser/animations';

import { routes } from './app.routes';
import { errorInterceptor } from './core/interceptors/error.interceptor';
import { authInterceptor } from './core/interceptors/auth.interceptor';
import { AuthService } from './core/services/auth.service';
import { SetupBootstrapService } from './features/setup/services/setup-bootstrap.service';
import { SetupErrorEnvelope } from './core/models/error-envelope';

const BOOTSTRAP_ERROR_STORAGE_KEY = 'sethlans.bootstrapError';

function initializeCsrf(): () => Promise<void> {
  const authService = inject(AuthService);
  return () =>
    new Promise<void>((resolve) => {
      authService.fetchCsrfToken().subscribe({
        next: () => resolve(),
        error: () => resolve(),
      });
    });
}

/**
 * Seams for unit tests. Tests override these to avoid touching real
 * window.location / window.history (non-configurable in modern browsers).
 */
export const _bootstrapSeams = {
  getSearch: (): string => window.location.search,
  stripQuery: (): void => {
    try {
      window.history.replaceState(null, '', window.location.pathname);
    } catch {
      // No-op if history API unavailable.
    }
  },
  redirect: (url: string): void => {
    window.location.replace(url);
  },
};

function redirectToBootstrapErrorPage(code: string, message: string): void {
  try {
    sessionStorage.setItem(
      BOOTSTRAP_ERROR_STORAGE_KEY,
      JSON.stringify({ code, message }),
    );
  } catch {
    // Ignore — bootstrap-error component falls back to generic copy.
  }
  _bootstrapSeams.redirect('/setup/bootstrap-error');
}

function handleBootstrapErrorViaSeam(err: unknown): void {
  if (err instanceof HttpErrorResponse) {
    const envelope = err.error as SetupErrorEnvelope | undefined;
    const code = envelope?.error?.code;
    const message = envelope?.error?.message;
    if (err.status === 403 && code === 'invalid_token') {
      redirectToBootstrapErrorPage(
        'invalid_token', message ?? 'Invalid setup token');
      return;
    }
    if (err.status === 429 || code === 'rate_limited') {
      redirectToBootstrapErrorPage(
        'rate_limited', message ?? 'Too many attempts');
      return;
    }
  }
  redirectToBootstrapErrorPage('network_error', 'Bootstrap request failed');
}

export function initializeSetupBootstrap(): () => Promise<void> {
  const bootstrapService = inject(SetupBootstrapService);
  return () =>
    new Promise<void>((resolve) => {
      let token: string | null = null;
      try {
        const params = new URLSearchParams(_bootstrapSeams.getSearch());
        token = params.get('token');
      } catch {
        token = null;
      }
      if (!token) {
        resolve();
        return;
      }
      bootstrapService.bootstrap(token).subscribe({
        next: () => {
          _bootstrapSeams.stripQuery();
          resolve();
        },
        error: (err) => {
          handleBootstrapErrorViaSeam(err);
          resolve();
        },
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
      })
    ),
    provideAnimations(),
    {
      provide: APP_INITIALIZER,
      useFactory: initializeCsrf,
      multi: true,
    },
    {
      provide: APP_INITIALIZER,
      useFactory: initializeSetupBootstrap,
      multi: true,
    },
  ],
};
