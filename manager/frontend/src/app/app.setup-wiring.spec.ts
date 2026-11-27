// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { DOCUMENT, Injector, runInInjectionContext } from '@angular/core';
import { LocationStrategy } from '@angular/common';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { appConfig, isSetupPath, primeCsrfCookie } from './app.config';
import { routes } from './app.routes';
import { AuthService } from './core/services/auth.service';
import { SetupPathLocationStrategy } from './features/setup/setup-location-strategy';
import { SetupPageComponent } from './features/setup/setup-page.component';

describe('setup page wiring (app.config, app.routes)', () => {
  /** FR-WZ-10: the setup page is /setup or below it, nothing else. */
  const paths: [string, boolean][] = [
    ['/setup', true], ['/setup/', true], ['/setup/x', true], ['/setupx', false], ['/', false],
  ];
  for (const [path, setup] of paths) {
    it(`treats ${path} as ${setup ? '' : 'not '}the setup page`, () => {
      expect(isSetupPath(path)).toBe(setup);
    });
  }

  /** FR-WZ-10, FR-SM-3: setup mode has no runtime API, so the CSRF start-up call is skipped on /setup/. */
  const primes: [string, number][] = [['/setup/', 0], ['/dashboard', 1]];
  for (const [path, calls] of primes) {
    it(`calls the CSRF endpoint ${calls} time(s) at start on ${path}`, async () => {
      const auth = jasmine.createSpyObj<AuthService>('AuthService', ['fetchCsrfToken']);
      auth.fetchCsrfToken.and.returnValue(of(void 0));
      TestBed.configureTestingModule({ providers: [{ provide: AuthService, useValue: auth }] });
      const injector = Injector.create({
        providers: [{ provide: DOCUMENT, useValue: { location: { pathname: path } } }],
        parent: TestBed.inject(Injector),
      });

      await runInInjectionContext(injector, () => primeCsrfCookie())();

      expect(auth.fetchCsrfToken).toHaveBeenCalledTimes(calls);
    });
  }

  /** FR-SM-3: the app's location strategy is the one that keeps /setup/'s slash. */
  it('provides SetupPathLocationStrategy as the LocationStrategy', () => {
    const provider = (appConfig.providers as unknown[]).find(
      p => (p as { provide?: unknown }).provide === LocationStrategy) as { useClass?: unknown };
    expect(provider?.useClass).toBe(SetupPathLocationStrategy);
  });

  /** FR-WZ-10: /setup is a top-level route with no guard and no layout. */
  it('routes /setup to the setup page without a guard or layout', async () => {
    const setup = routes.find(route => route.path === 'setup')!;
    expect(setup.canActivate).toBeUndefined();
    expect(setup.canMatch).toBeUndefined();
    expect(setup.children).toBeUndefined();
    expect(await setup.loadComponent!()).toBe(SetupPageComponent);
    const nested = routes.flatMap(route => route.children ?? []).some(route => route.path === 'setup');
    expect(nested).toBeFalse();
  });
});
