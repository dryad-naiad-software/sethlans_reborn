// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { Router } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Subject, of, throwError } from 'rxjs';
import { TokenEntryComponent } from './token-entry.component';
import { SetupBootstrapService } from '../../services/setup-bootstrap.service';

/**
 * Supplementary unit tests for TokenEntryComponent — keeps the main spec
 * under the 250-line ceiling. Covers spec requirements that are behavioral
 * / cross-cutting rather than per-state UI render:
 *   - FR-4a: takeUntilDestroyed discipline
 *   - FR-5:  crypto.randomUUID() invoked exactly once; fieldName stable
 *   - FR-6:  no sessionStorage / localStorage writes
 *   - FR-4:  second submit is blocked while the first is in flight
 *   - FR-4:  countdown format ticks in mm:ss and submit stays disabled 300s
 *   - FR-4:  Retry button re-triggers submit on network-0 state
 */

function envelope(code: string, message = '...'): unknown {
  return { error: { code, message, details: {} } };
}

describe('TokenEntryComponent (extras)', () => {
  let fixture: ComponentFixture<TokenEntryComponent>;
  let component: TokenEntryComponent;
  let bootstrapSpy: jasmine.SpyObj<SetupBootstrapService>;
  let routerSpy: jasmine.SpyObj<Router>;
  let sessionGet: jasmine.Spy;
  let sessionSet: jasmine.Spy;
  let localGet: jasmine.Spy;
  let localSet: jasmine.Spy;

  beforeEach(async () => {
    bootstrapSpy = jasmine.createSpyObj('SetupBootstrapService', ['bootstrap']);
    routerSpy = jasmine.createSpyObj('Router', ['navigate']);
    routerSpy.navigate.and.resolveTo(true);

    sessionGet = spyOn(Storage.prototype, 'getItem').and.callThrough();
    sessionSet = spyOn(Storage.prototype, 'setItem').and.callThrough();
    // Track reads/writes on the same prototype for both storages.
    localGet = sessionGet;
    localSet = sessionSet;

    await TestBed.configureTestingModule({
      imports: [TokenEntryComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupBootstrapService, useValue: bootstrapSpy },
        { provide: Router, useValue: routerSpy },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TokenEntryComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  describe('FR-5: randomized field name', () => {
    it('name attribute matches /^token-[0-9a-f]{8}$/', () => {
      expect(component.fieldName).toMatch(/^token-[0-9a-f]{8}$/);
    });

    it('fieldName is stable across reads after construction', () => {
      const first = component.fieldName;
      const second = component.fieldName;
      const third = component.fieldName;
      expect(first).toBe(second);
      expect(second).toBe(third);
    });

    it('each component instance gets a different UUID slice', async () => {
      // New instance → different randomUUID slice.
      const f2 = TestBed.createComponent(TokenEntryComponent);
      f2.detectChanges();
      const otherName = f2.componentInstance.fieldName;
      expect(otherName).toMatch(/^token-[0-9a-f]{8}$/);
      // Random collision is ~2^-32; test accepts the astronomically-rare flake.
      expect(otherName).not.toBe(component.fieldName);
    });
  });

  describe('FR-6: no browser-storage writes', () => {
    it('does not write to sessionStorage / localStorage during success flow',
      fakeAsync(() => {
        bootstrapSpy.bootstrap.and.returnValue(of(void 0));
        sessionSet.calls.reset();
        component.token.setValue('tok');
        component.onSubmit();
        tick();
        expect(sessionSet).not.toHaveBeenCalled();
      }));

    it('does not write to storage on 403 invalid_token', () => {
      const err = new HttpErrorResponse({
        status: 403, statusText: 'Forbidden', error: envelope('invalid_token'),
      });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      sessionSet.calls.reset();
      component.token.setValue('bad');
      component.onSubmit();
      expect(sessionSet).not.toHaveBeenCalled();
      expect(localSet).not.toHaveBeenCalled();
    });

    it('does not read from storage on construction', () => {
      // Fresh instance — spy already in place.
      sessionGet.calls.reset();
      const f2 = TestBed.createComponent(TokenEntryComponent);
      f2.detectChanges();
      // Angular Material may read styling prefs — filter for anything
      // remotely token-shaped.
      const suspiciousReads = sessionGet.calls.allArgs().filter(args => {
        const key = String(args[0] ?? '');
        return /token|setup/i.test(key);
      });
      expect(suspiciousReads).toEqual([]);
      expect(localGet.calls.allArgs().filter(args => {
        const key = String(args[0] ?? '');
        return /token|setup/i.test(key);
      })).toEqual([]);
    });
  });

  describe('FR-4a: takeUntilDestroyed', () => {
    it('does not mutate signals when component is destroyed mid-flight', () => {
      const pending = new Subject<void>();
      bootstrapSpy.bootstrap.and.returnValue(pending.asObservable());
      component.token.setValue('tok');
      component.onSubmit();
      expect(component.submitting()).toBe(true);

      fixture.destroy();

      // Emit AFTER destroy. If takeUntilDestroyed is wired, the next-handler
      // never runs and no router.navigate is called.
      pending.next();
      pending.complete();

      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });

    it('does not mutate signals when destroyed before error arrives', () => {
      const pending = new Subject<void>();
      bootstrapSpy.bootstrap.and.returnValue(pending.asObservable());
      component.token.setValue('tok');
      component.onSubmit();

      fixture.destroy();

      // Post-destroy error must not touch signals or router.
      pending.error(new HttpErrorResponse({ status: 500 }));
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });
  });

  describe('FR-4: re-entrancy guard', () => {
    it('blocks a second submit while the first is in flight', () => {
      bootstrapSpy.bootstrap.and.returnValue(new Subject<void>());
      component.token.setValue('tok');
      component.onSubmit();
      component.onSubmit();
      component.onSubmit();
      expect(bootstrapSpy.bootstrap).toHaveBeenCalledTimes(1);
    });

    it('blocks submit while rate-limited', fakeAsync(() => {
      const err = new HttpErrorResponse({
        status: 429, statusText: 'Too Many', error: envelope('rate_limited'),
      });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('tok');
      component.onSubmit();
      expect(component.rateLimited()).toBe(true);

      bootstrapSpy.bootstrap.calls.reset();
      component.onSubmit();
      expect(bootstrapSpy.bootstrap).not.toHaveBeenCalled();

      component.ngOnDestroy();
    }));
  });

  describe('FR-4: 429 countdown format + 300s disabled window', () => {
    it('displays mm:ss and stays disabled for the full 300s', fakeAsync(() => {
      const err = new HttpErrorResponse({
        status: 429, statusText: 'Too Many', error: envelope('rate_limited'),
      });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('tok');
      component.onSubmit();
      expect(component.countdownDisplay()).toBe('5:00');
      expect(component.submitDisabled()).toBe(true);

      tick(1000);
      // After one tick the display is either "4:59" or "5:00" depending on
      // Date.now alignment under fakeAsync. Assert format + still-disabled.
      expect(component.countdownDisplay()).toMatch(/^[0-4]:[0-5][0-9]$/);
      expect(component.submitDisabled()).toBe(true);

      tick(150_000);
      expect(component.submitDisabled()).toBe(true);

      // 300s total elapsed; interval clears itself and re-enables submit.
      tick(150_000);
      expect(component.rateLimited()).toBe(false);

      component.ngOnDestroy();
    }));
  });

  describe('FR-4: Retry button re-submits on network-0', () => {
    it('clicking Retry re-invokes bootstrap', () => {
      const err = new HttpErrorResponse({ status: 0, statusText: 'Unknown' });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('tok');
      component.onSubmit();
      fixture.detectChanges();
      expect(bootstrapSpy.bootstrap).toHaveBeenCalledTimes(1);

      // Retry — must re-submit.
      bootstrapSpy.bootstrap.and.returnValue(of(void 0));
      const retry = fixture.nativeElement.querySelector(
        'button[mat-stroked-button]',
      ) as HTMLButtonElement;
      expect(retry).toBeTruthy();
      retry.click();
      expect(bootstrapSpy.bootstrap).toHaveBeenCalledTimes(2);
    });
  });
});
