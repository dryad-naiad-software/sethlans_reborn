// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed, fakeAsync, flushMicrotasks } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { of, throwError, EMPTY } from 'rxjs';
import { initializeSetupBootstrap, _bootstrapSeams } from './app.config';
import { SetupBootstrapService } from './features/setup/services/setup-bootstrap.service';

const STORAGE_KEY = 'sethlans.bootstrapError';

describe('initializeSetupBootstrap (APP_INITIALIZER factory)', () => {
  let mockBootstrap: jasmine.SpyObj<SetupBootstrapService>;
  const originalSeams = { ..._bootstrapSeams };
  let stripQuerySpy: jasmine.Spy;
  let redirectSpy: jasmine.Spy;

  function setSearch(search: string): void {
    _bootstrapSeams.getSearch = () => search;
  }

  beforeEach(() => {
    sessionStorage.clear();
    stripQuerySpy = jasmine.createSpy('stripQuery');
    redirectSpy = jasmine.createSpy('redirect');
    _bootstrapSeams.getSearch = () => '';
    _bootstrapSeams.stripQuery = stripQuerySpy;
    _bootstrapSeams.redirect = redirectSpy;

    mockBootstrap = jasmine.createSpyObj('SetupBootstrapService', ['bootstrap']);
    TestBed.configureTestingModule({
      providers: [
        { provide: SetupBootstrapService, useValue: mockBootstrap },
      ],
    });
  });

  afterEach(() => {
    sessionStorage.clear();
    _bootstrapSeams.getSearch = originalSeams.getSearch;
    _bootstrapSeams.stripQuery = originalSeams.stripQuery;
    _bootstrapSeams.redirect = originalSeams.redirect;
  });

  function run(): Promise<void> {
    const factory = TestBed.runInInjectionContext(() => initializeSetupBootstrap());
    return factory();
  }

  it('resolves without calling bootstrap when no ?token= in URL', async () => {
    setSearch('');
    await run();
    expect(mockBootstrap.bootstrap).not.toHaveBeenCalled();
  });

  it('calls bootstrap with extracted token and strips the query string', async () => {
    setSearch('?token=setup-token-abc');
    mockBootstrap.bootstrap.and.returnValue(of(void 0));

    await run();

    expect(mockBootstrap.bootstrap).toHaveBeenCalledWith('setup-token-abc');
    expect(stripQuerySpy).toHaveBeenCalled();
    expect(redirectSpy).not.toHaveBeenCalled();
  });

  it('on 403 invalid_token: stores envelope and redirects to bootstrap-error',
    fakeAsync(() => {
      setSearch('?token=bad');
      const err = new HttpErrorResponse({
        status: 403,
        statusText: 'Forbidden',
        error: {
          error: {
            code: 'invalid_token',
            message: 'Invalid setup token',
            details: {},
          },
        },
      });
      mockBootstrap.bootstrap.and.returnValue(throwError(() => err));

      run();
      flushMicrotasks();

      const stored = sessionStorage.getItem(STORAGE_KEY);
      expect(stored).not.toBeNull();
      const parsed = JSON.parse(stored!);
      expect(parsed.code).toBe('invalid_token');
      expect(parsed.message).toBe('Invalid setup token');
      expect(redirectSpy).toHaveBeenCalledWith('/setup/bootstrap-error');
    }),
  );

  it('on 429 rate_limited: stores code and redirects', fakeAsync(() => {
    setSearch('?token=x');
    const err = new HttpErrorResponse({
      status: 429,
      statusText: 'Too Many Requests',
      error: {
        error: { code: 'rate_limited', message: 'Too many', details: {} },
      },
    });
    mockBootstrap.bootstrap.and.returnValue(throwError(() => err));

    run();
    flushMicrotasks();

    const stored = JSON.parse(sessionStorage.getItem(STORAGE_KEY)!);
    expect(stored.code).toBe('rate_limited');
    expect(redirectSpy).toHaveBeenCalledWith('/setup/bootstrap-error');
  }));

  it('on network error (status 0): stores network_error code', fakeAsync(() => {
    setSearch('?token=x');
    const err = new HttpErrorResponse({
      status: 0, statusText: 'Unknown', error: new ProgressEvent('error'),
    });
    mockBootstrap.bootstrap.and.returnValue(throwError(() => err));

    run();
    flushMicrotasks();

    const stored = JSON.parse(sessionStorage.getItem(STORAGE_KEY)!);
    expect(stored.code).toBe('network_error');
    expect(redirectSpy).toHaveBeenCalledWith('/setup/bootstrap-error');
  }));

  it('does not redirect when bootstrap succeeds', async () => {
    setSearch('?token=good');
    mockBootstrap.bootstrap.and.returnValue(of(void 0));
    await run();
    expect(redirectSpy).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('ignores non-token query params', async () => {
    setSearch('?other=x');
    mockBootstrap.bootstrap.and.returnValue(EMPTY);
    await run();
    expect(mockBootstrap.bootstrap).not.toHaveBeenCalled();
  });
});
