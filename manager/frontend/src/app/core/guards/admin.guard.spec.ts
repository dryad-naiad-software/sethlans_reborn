// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { BehaviorSubject, of, throwError } from 'rxjs';
import { adminGuard } from './admin.guard';
import { AuthService, UserInfo } from '../services/auth.service';

describe('adminGuard', () => {
  let routerSpy: jasmine.SpyObj<Router>;
  let authStub: AuthServiceStub;

  /**
   * Stub for AuthService that exposes the same surface (`user` getter,
   * `getCurrentUser()`, `isAuthenticated$`) the real service does.
   * The `user` getter is backed by a settable `_user` field so each test
   * can seed staff / non-staff / null state without poking internals.
   */
  class AuthServiceStub {
    _user: UserInfo | null = null;
    isAuthenticated$ = new BehaviorSubject<boolean>(false);
    getCurrentUser = jasmine.createSpy('getCurrentUser');
    get user(): UserInfo | null {
      return this._user;
    }
  }

  beforeEach(() => {
    routerSpy = jasmine.createSpyObj('Router', ['navigate']);
    authStub = new AuthServiceStub();

    TestBed.configureTestingModule({
      providers: [
        { provide: Router, useValue: routerSpy },
        { provide: AuthService, useValue: authStub },
      ],
    });
  });

  function runGuard(): boolean | Promise<boolean> {
    return TestBed.runInInjectionContext(() =>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (adminGuard as any)({}, {}),
    ) as boolean | Promise<boolean>;
  }

  /** Helper: subscribe to the guard observable and resolve the first value. */
  async function runGuardAsync(): Promise<boolean> {
    const result = runGuard();
    if (typeof result === 'boolean') return result;
    // CanActivateFn may also return Observable<boolean> | Promise<boolean>;
    // pipe through a Promise to await any async path.
    return await new Promise<boolean>((resolve, reject) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const r: any = result;
      if (r && typeof r.subscribe === 'function') {
        r.subscribe({ next: (v: boolean) => resolve(v), error: reject });
      } else if (r && typeof r.then === 'function') {
        r.then(resolve, reject);
      } else {
        resolve(r as boolean);
      }
    });
  }

  describe('user already loaded', () => {
    it('should allow navigation when user is staff', () => {
      authStub._user = { username: 'admin', is_staff: true };
      const result = runGuard();
      expect(result).toBeTrue();
      expect(routerSpy.navigate).not.toHaveBeenCalled();
      expect(authStub.getCurrentUser).not.toHaveBeenCalled();
    });

    it('should redirect to / and return false for non-staff user', () => {
      authStub._user = { username: 'alice', is_staff: false };
      const result = runGuard();
      expect(result).toBeFalse();
      expect(routerSpy.navigate).toHaveBeenCalledOnceWith(['/']);
    });
  });

  describe('user not yet loaded — fetch via getCurrentUser()', () => {
    it('should allow navigation when fetch resolves to staff user', async () => {
      authStub.getCurrentUser.and.returnValue(
        of({ username: 'admin', is_staff: true } as UserInfo),
      );
      const result = await runGuardAsync();
      expect(result).toBeTrue();
      expect(authStub.getCurrentUser).toHaveBeenCalledTimes(1);
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });

    it('should redirect to / when fetch resolves to non-staff user', async () => {
      authStub.getCurrentUser.and.returnValue(
        of({ username: 'bob', is_staff: false } as UserInfo),
      );
      const result = await runGuardAsync();
      expect(result).toBeFalse();
      expect(routerSpy.navigate).toHaveBeenCalledOnceWith(['/']);
    });

    it('should redirect to /login on getCurrentUser() failure', async () => {
      authStub.getCurrentUser.and.returnValue(
        throwError(() => new Error('401')),
      );
      const result = await runGuardAsync();
      expect(result).toBeFalse();
      expect(routerSpy.navigate).toHaveBeenCalledOnceWith(['/login']);
    });
  });
});
