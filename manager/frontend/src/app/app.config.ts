// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  ApplicationConfig,
  provideZoneChangeDetection,
  APP_INITIALIZER,
  inject,
} from '@angular/core';
import { provideRouter, Router } from '@angular/router';
import {
  HttpClient,
  provideHttpClient,
  withInterceptors,
  withXsrfConfiguration,
} from '@angular/common/http';
import { provideAnimations } from '@angular/platform-browser/animations';

import { routes } from './app.routes';
import { errorInterceptor } from './core/interceptors/error.interceptor';
import { authInterceptor } from './core/interceptors/auth.interceptor';
import { AuthService } from './core/services/auth.service';
import { SetupStatus } from './features/setup/models/setup.models';
import { environment } from '../environments/environment';

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

function initializeSetupCheck(): () => Promise<void> {
  const http = inject(HttpClient);
  const router = inject(Router);
  return () =>
    new Promise<void>((resolve) => {
      http.get<SetupStatus>(`${environment.apiBaseUrl}/setup/status/`).subscribe({
        next: (status) => {
          if (!status.complete) {
            router.navigate(['/setup']);
          }
          resolve();
        },
        error: (err) => {
          if (err.status === 503) {
            router.navigate(['/setup']);
          }
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
      useFactory: initializeSetupCheck,
      multi: true,
    },
  ],
};
