// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { Router } from '@angular/router';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { TokenEntryComponent } from './token-entry.component';
import { environment } from '../../../../../environments/environment';

/**
 * Regression coverage for issue #112 — token-entry form submission.
 *
 * Prior to the fix the `<form>` had `(ngSubmit)` but no `[formGroup]`, the
 * input used `[formControl]=` without ReactiveFormsModule binding it to the
 * form, and FormsModule was not imported. Clicking the submit button
 * triggered a native form submission (page reload) instead of firing
 * (ngSubmit) and POSTing to /api/setup/bootstrap/.
 *
 * These tests exercise the real form-submission path (button click -> submit
 * event) and assert:
 *   1. Angular's form directive calls preventDefault() on the native submit
 *      event (no page reload).
 *   2. A POST to /api/setup/bootstrap/ is issued with the typed token value.
 *
 * Both assertions fail against the pre-fix template.
 */
describe('TokenEntryComponent (form submit regression — issue #112)', () => {
  let fixture: ComponentFixture<TokenEntryComponent>;
  let component: TokenEntryComponent;
  let httpMock: HttpTestingController;
  let routerSpy: jasmine.SpyObj<Router>;

  const bootstrapUrl = `${environment.apiBaseUrl}/setup/bootstrap/`;

  beforeEach(async () => {
    routerSpy = jasmine.createSpyObj('Router', ['navigate']);
    routerSpy.navigate.and.resolveTo(true);

    await TestBed.configureTestingModule({
      imports: [TokenEntryComponent, NoopAnimationsModule],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: Router, useValue: routerSpy },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TokenEntryComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  afterEach(() => {
    httpMock.verify();
  });

  function getInput(): HTMLInputElement {
    return fixture.nativeElement.querySelector('input[matInput]') as HTMLInputElement;
  }

  function getForm(): HTMLFormElement {
    return fixture.nativeElement.querySelector('form') as HTMLFormElement;
  }

  function getSubmitButton(): HTMLButtonElement {
    return fixture.nativeElement.querySelector('button[type="submit"]') as HTMLButtonElement;
  }

  /** Types a value into the input via the real input-event path. */
  function typeToken(value: string): void {
    const input = getInput();
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  it('clicking the submit button prevents default and POSTs the token', fakeAsync(() => {
    typeToken('my-setup-token');

    // Capture the native submit event so we can assert defaultPrevented.
    let capturedEvent: SubmitEvent | null = null;
    getForm().addEventListener(
      'submit',
      (ev) => {
        capturedEvent = ev as SubmitEvent;
      },
      // Capture phase so we observe the event BEFORE Angular's own handler
      // calls preventDefault(), then re-check after the click settles.
      { capture: false },
    );

    getSubmitButton().click();
    tick();

    // Angular's FormGroupDirective must have preventDefault'd the native
    // submission. If [formGroup] is missing the browser will try to submit
    // the form (reload), and `defaultPrevented` will be false.
    expect(capturedEvent).not.toBeNull();
    expect(capturedEvent!.defaultPrevented).toBe(true);

    // And the POST must hit /api/setup/bootstrap/ with the typed value.
    const req = httpMock.expectOne(bootstrapUrl);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ token: 'my-setup-token' });
    req.flush(null, { status: 204, statusText: 'No Content' });
    tick();
  }));

  it('trims whitespace from the token before POSTing', fakeAsync(() => {
    typeToken('  padded-token  ');
    getSubmitButton().click();
    tick();

    const req = httpMock.expectOne(bootstrapUrl);
    expect(req.request.body).toEqual({ token: 'padded-token' });
    req.flush(null, { status: 204, statusText: 'No Content' });
    tick();
  }));

  it('FormGroup binds the input: typing updates form.controls.token', () => {
    typeToken('bound');
    expect(component.form.controls.token.value).toBe('bound');
    // Sanity-check: the backing control the submit handler reads is the
    // same one ReactiveForms mutates on input events.
    expect(component.token.value).toBe('bound');
  });
});
