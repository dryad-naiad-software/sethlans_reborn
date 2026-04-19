// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { Router, UrlTree } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { of, throwError } from 'rxjs';
import { setupSessionGuard } from './setup-session.guard';
import { SetupApiService } from '../services/setup-api.service';

describe('setupSessionGuard', () => {
  let apiSpy: jasmine.SpyObj<SetupApiService>;
  let routerSpy: jasmine.SpyObj<Router>;

  beforeEach(() => {
    apiSpy = jasmine.createSpyObj('SetupApiService', ['getSetupSession']);
    routerSpy = jasmine.createSpyObj('Router', ['createUrlTree']);
    (routerSpy.createUrlTree as jasmine.Spy).and.returnValue(
      {} as UrlTree,
    );

    TestBed.configureTestingModule({
      providers: [
        { provide: SetupApiService, useValue: apiSpy },
        { provide: Router, useValue: routerSpy },
      ],
    });
  });

  function runGuard(): Promise<boolean | UrlTree> {
    return TestBed.runInInjectionContext(() =>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (setupSessionGuard as any)({}, {}),
    ) as Promise<boolean | UrlTree>;
  }

  it('returns true when probe resolves (204)', async () => {
    apiSpy.getSetupSession.and.returnValue(of(void 0));
    const result = await runGuard();
    expect(result).toBe(true);
    expect(routerSpy.createUrlTree).not.toHaveBeenCalled();
  });

  it('returns UrlTree(/setup) on 403 setup_in_progress', async () => {
    const err = new HttpErrorResponse({
      status: 403,
      statusText: 'Forbidden',
      error: {
        error: { code: 'setup_in_progress', message: '...', details: {} },
      },
    });
    apiSpy.getSetupSession.and.returnValue(throwError(() => err));
    const result = await runGuard();
    expect(result).toEqual({} as UrlTree);
    expect(routerSpy.createUrlTree).toHaveBeenCalledOnceWith(['/setup']);
  });

  it('returns UrlTree(/setup) on 500 server error', async () => {
    const err = new HttpErrorResponse({ status: 500, statusText: 'Error' });
    apiSpy.getSetupSession.and.returnValue(throwError(() => err));
    const result = await runGuard();
    expect(result).toEqual({} as UrlTree);
    expect(routerSpy.createUrlTree).toHaveBeenCalledOnceWith(['/setup']);
  });

  it('returns UrlTree(/setup) on network 0', async () => {
    const err = new HttpErrorResponse({ status: 0, statusText: 'Unknown' });
    apiSpy.getSetupSession.and.returnValue(throwError(() => err));
    const result = await runGuard();
    expect(result).toEqual({} as UrlTree);
    expect(routerSpy.createUrlTree).toHaveBeenCalledOnceWith(['/setup']);
  });

  it('returns UrlTree(/setup) on unexpected error code', async () => {
    const err = new HttpErrorResponse({
      status: 418,
      statusText: 'Teapot',
      error: { error: { code: 'what', message: '...', details: {} } },
    });
    apiSpy.getSetupSession.and.returnValue(throwError(() => err));
    const result = await runGuard();
    expect(result).toEqual({} as UrlTree);
    expect(routerSpy.createUrlTree).toHaveBeenCalledOnceWith(['/setup']);
  });
});
