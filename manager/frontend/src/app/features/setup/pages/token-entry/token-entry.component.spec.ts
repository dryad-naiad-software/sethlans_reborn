// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick, flush } from '@angular/core/testing';
import { Router } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Subject, of, throwError } from 'rxjs';
import { TokenEntryComponent } from './token-entry.component';
import { SetupBootstrapService } from '../../services/setup-bootstrap.service';

function envelope(code: string, message = '...'): unknown {
  return { error: { code, message, details: {} } };
}

describe('TokenEntryComponent', () => {
  let fixture: ComponentFixture<TokenEntryComponent>;
  let component: TokenEntryComponent;
  let bootstrapSpy: jasmine.SpyObj<SetupBootstrapService>;
  let routerSpy: jasmine.SpyObj<Router>;

  beforeEach(async () => {
    bootstrapSpy = jasmine.createSpyObj('SetupBootstrapService', ['bootstrap']);
    routerSpy = jasmine.createSpyObj('Router', ['navigate']);
    routerSpy.navigate.and.resolveTo(true);

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

  function getInput(): HTMLInputElement {
    return fixture.nativeElement.querySelector('input[matInput]') as HTMLInputElement;
  }

  function getSubmitButton(): HTMLButtonElement {
    return fixture.nativeElement.querySelector('button[type="submit"]') as HTMLButtonElement;
  }

  function getToggleButton(): HTMLButtonElement {
    return fixture.nativeElement.querySelector('button[matSuffix]') as HTMLButtonElement;
  }

  describe('Default state', () => {
    it('renders empty field with Continue disabled', () => {
      expect(getInput().value).toBe('');
      expect(getSubmitButton().disabled).toBe(true);
    });

    it('has security-hardened input attributes', () => {
      const i = getInput();
      expect(i.type).toBe('password');
      expect(i.getAttribute('autocomplete')).toBe('one-time-code');
      expect(i.getAttribute('spellcheck')).toBe('false');
      expect(i.getAttribute('autocapitalize')).toBe('off');
      expect(i.getAttribute('autocorrect')).toBe('off');
      expect(i.getAttribute('name')).toMatch(/^token-[0-9a-f]{8}$/);
    });

    it('toggle has aria-pressed="false" and aria-label "Show token"', () => {
      const btn = getToggleButton();
      expect(btn.getAttribute('aria-pressed')).toBe('false');
      expect(btn.getAttribute('aria-label')).toBe('Show token');
    });
  });

  describe('Token pasted + show-toggle pressed', () => {
    it('enables Continue once the field has a value', () => {
      component.token.setValue('my-token');
      fixture.detectChanges();
      expect(getSubmitButton().disabled).toBe(false);
    });

    it('toggles type=text and aria-pressed=true when toggle is pressed', () => {
      getToggleButton().click();
      fixture.detectChanges();
      expect(getInput().type).toBe('text');
      const btn = getToggleButton();
      expect(btn.getAttribute('aria-pressed')).toBe('true');
      expect(btn.getAttribute('aria-label')).toBe('Hide token');
    });
  });

  describe('Submitting', () => {
    it('sets submitting=true and disables the field while POST is in flight', () => {
      // Return a never-completing observable to stay in-flight.
      bootstrapSpy.bootstrap.and.returnValue(new Subject<void>());
      component.token.setValue('tok');
      component.onSubmit();
      fixture.detectChanges();
      expect(component.submitting()).toBe(true);
      expect(getInput().readOnly).toBe(true);
    });

    it('POSTs with the trimmed token value', () => {
      bootstrapSpy.bootstrap.and.returnValue(of(void 0));
      component.token.setValue('  abc  ');
      component.onSubmit();
      expect(bootstrapSpy.bootstrap).toHaveBeenCalledOnceWith('abc');
    });
  });

  describe('Success (204)', () => {
    it('navigates to /setup/wizard exactly once', fakeAsync(() => {
      bootstrapSpy.bootstrap.and.returnValue(of(void 0));
      component.token.setValue('good');
      component.onSubmit();
      tick();
      expect(routerSpy.navigate).toHaveBeenCalledOnceWith(['/setup/wizard']);
    }));

    it('shows "Setup started. Reload to continue." when navigation rejects',
      fakeAsync(() => {
        routerSpy.navigate.and.rejectWith(new Error('chunk-load'));
        bootstrapSpy.bootstrap.and.returnValue(of(void 0));
        component.token.setValue('good');
        component.onSubmit();
        tick();
        expect(component.errorMessage()).toBe('Setup started. Reload to continue.');
        expect(component.submitDisabled()).toBe(true);
      }));

    it('shows "Setup started. Reload to continue." when navigate resolves false',
      fakeAsync(() => {
        routerSpy.navigate.and.resolveTo(false);
        bootstrapSpy.bootstrap.and.returnValue(of(void 0));
        component.token.setValue('good');
        component.onSubmit();
        tick();
        expect(component.errorMessage()).toBe('Setup started. Reload to continue.');
      }));
  });

  describe('403 invalid_token', () => {
    it('shows inline "Invalid token..." message', () => {
      const err = new HttpErrorResponse({
        status: 403,
        statusText: 'Forbidden',
        error: envelope('invalid_token'),
      });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('bad');
      component.onSubmit();
      expect(component.errorMessage()).toBe('Invalid token. Check and retry.');
    });

    it('refocuses the token field', fakeAsync(() => {
      const err = new HttpErrorResponse({
        status: 403, statusText: 'Forbidden', error: envelope('invalid_token'),
      });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      const focusSpy = spyOn(getInput(), 'focus');
      component.token.setValue('bad');
      component.onSubmit();
      flush();
      expect(focusSpy).toHaveBeenCalled();
    }));
  });

  describe('429 rate_limited', () => {
    it('disables submit and starts a 300s countdown', fakeAsync(() => {
      const err = new HttpErrorResponse({
        status: 429, statusText: 'Too Many', error: envelope('rate_limited'),
      });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('x');
      component.onSubmit();
      expect(component.rateLimited()).toBe(true);
      expect(component.countdownSeconds()).toBe(300);
      expect(component.submitDisabled()).toBe(true);
      tick(1000);
      expect(component.countdownSeconds()).toBeLessThan(300);
      component.ngOnDestroy();
    }));

    it('clears rate-limit when countdown reaches zero', fakeAsync(() => {
      const err = new HttpErrorResponse({
        status: 429, statusText: 'Too Many', error: envelope('rate_limited'),
      });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('x');
      component.onSubmit();
      tick(301_000);
      expect(component.rateLimited()).toBe(false);
      component.ngOnDestroy();
    }));
  });

  describe('Network 0', () => {
    it('shows inline message and a Retry button', () => {
      const err = new HttpErrorResponse({ status: 0, statusText: 'Unknown' });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('x');
      component.onSubmit();
      fixture.detectChanges();
      expect(component.errorMessage()).toContain('Cannot reach the manager');
      expect(component.showRetry()).toBe(true);
      const retry = fixture.nativeElement.querySelector(
        'button[mat-stroked-button]',
      );
      expect(retry).toBeTruthy();
    });
  });

  describe('Other 4xx/5xx', () => {
    it('shows generic service-unavailable copy', () => {
      const err = new HttpErrorResponse({ status: 500, statusText: 'ISE' });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('x');
      component.onSubmit();
      expect(component.errorMessage()).toBe('Setup service unavailable. Reload to retry.');
    });
  });

  describe('No local branch for 404 setup_complete', () => {
    it('falls through to generic message (interceptor handles it)', () => {
      const err = new HttpErrorResponse({
        status: 404, statusText: 'Not Found', error: envelope('setup_complete'),
      });
      bootstrapSpy.bootstrap.and.returnValue(throwError(() => err));
      component.token.setValue('x');
      component.onSubmit();
      expect(component.errorMessage()).toBe('Setup service unavailable. Reload to retry.');
    });
  });

});
