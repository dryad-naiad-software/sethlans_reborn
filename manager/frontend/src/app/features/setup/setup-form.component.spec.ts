// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of, throwError } from 'rxjs';
import { SetupApiService } from './setup-api.service';
import { SetupFormComponent } from './setup-form.component';
import { finishInfo, setupForm } from './setup.fixtures';
import { FieldErrors, SetupFailure, SetupForm } from './setup.types';

const SECTION_TAGS = ['app-setup-admin-section', 'app-setup-advanced-section',
  'app-setup-worker-section', 'app-setup-manager-section', 'app-setup-data-folder-section'];

function failure(status: number, code: string, message: string, fields: FieldErrors = {}): SetupFailure {
  return { status, code, message, fields, codes: {} };
}

describe('SetupFormComponent', () => {
  let fixture: ComponentFixture<SetupFormComponent>;
  let component: SetupFormComponent;
  let api: jasmine.SpyObj<SetupApiService>;
  let form: SetupForm;

  beforeEach(async () => {
    api = jasmine.createSpyObj('SetupApiService', ['submit', 'clearSession']);
    await TestBed.configureTestingModule({
      imports: [SetupFormComponent, NoopAnimationsModule],
      providers: [{ provide: SetupApiService, useValue: api }],
    }).compileComponents();
    fixture = TestBed.createComponent(SetupFormComponent);
    component = fixture.componentInstance;
    form = setupForm();
    fixture.componentRef.setInput('form', form);
  });

  function text(selector: string): string {
    return (fixture.nativeElement.querySelector(selector) as HTMLElement | null)?.textContent ?? '';
  }

  function shownSections(): string[] {
    return SECTION_TAGS.filter(tag => fixture.nativeElement.querySelector(tag) !== null);
  }

  /** FR-LN-6, #348: the full reason after the token, under a label that does not repeat it. */
  it('#348 shows the full last_failure reason once, with no doubled prefix', () => {
    form.last_failure = 'First start failed: the admin password is too short';
    fixture.detectChanges();
    expect(text('.setup-error').trim())
      .toBe('The last start ended with this error: First start failed: the admin password is too short');
  });

  /** FR-WZ-3: the rendered sections follow the chosen mode. */
  it('renders the sections of the chosen mode', () => {
    fixture.detectChanges();
    expect(shownSections()).toEqual(['app-setup-admin-section', 'app-setup-advanced-section',
      'app-setup-worker-section', 'app-setup-data-folder-section']);

    form.mode = 'worker';
    fixture.detectChanges();
    expect(shownSections()).toEqual(['app-setup-worker-section', 'app-setup-manager-section',
      'app-setup-data-folder-section']);
  });

  /** FR-WZ-2, OQ-9: a 400 shows each message by its dotted key and opens Advanced for its fields. */
  it('shows field errors in their sections and opens Advanced', () => {
    api.submit.and.returnValue(throwError(() => failure(400, 'invalid', 'The setup server answered 400.', {
      'server.port': ['Port 8080 is in use.'], 'data_folder.path': ['Use an absolute path.'],
    })));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('mat-expansion-panel.mat-expanded')).toBeNull();

    component.submit();
    fixture.detectChanges();

    expect(text('app-setup-advanced-section')).toContain('Port 8080 is in use.');
    expect(text('app-setup-data-folder-section')).toContain('Use an absolute path.');
    expect(fixture.nativeElement.querySelector('mat-expansion-panel.mat-expanded')).not.toBeNull();
    expect(component.message).toBe('');
    expect(fixture.nativeElement.textContent).toContain('Some fields need a change');
    // S4: field messages are not alerts; the one summary line is.
    expect(fixture.nativeElement.querySelectorAll('app-setup-field-errors [role=alert]').length).toBe(0);
  });

  /** Review S4: after a failed submit the focus goes to the one role="alert" summary line. */
  const summaries: { why: string; edit: (f: SetupForm, c: SetupFormComponent) => void; line: string }[] = [
    { why: 'field errors', line: 'Some fields need a change',
      edit: () => api.submit.and.returnValue(throwError(() => failure(400, 'invalid', '',
        { 'data_folder.path': ['Use an absolute path.'] }))) },
    { why: 'a 409', line: 'Setup is already being saved',
      edit: () => api.submit.and.returnValue(throwError(() => failure(409, 'conflict',
        'Setup is already being saved, or was saved.'))) },
    { why: 'a password mismatch', line: 'The worker page passwords are not the same.',
      edit: (f, c) => { f.mode = 'worker'; f.worker.ui_password = 'pw-1'; c.confirm.worker = 'pw-2'; } },
  ];
  for (const c of summaries) {
    it(`focuses the summary alert after ${c.why}`, () => {
      c.edit(form, component);
      fixture.detectChanges();

      component.submit();
      fixture.detectChanges();
      TestBed.tick(); // runs the afterNextRender that moves the focus

      const focused = document.activeElement as HTMLElement;
      expect(focused.getAttribute('role')).toBe('alert');
      expect(focused.textContent).toContain(c.line);
    });
  }

  /** FR-WZ-3: errors left from a section the mode now hides are not shown. */
  it('does not show stale errors of a hidden section', () => {
    api.submit.and.returnValue(throwError(() => failure(400, 'invalid', '', {
      'admin.username': ['This field is required.'],
    })));
    fixture.detectChanges();
    component.submit();
    form.mode = 'worker';
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain('This field is required.');
    expect(fixture.nativeElement.textContent).not.toContain('Some fields need a change');
  });

  /** FR-WZ-2, OQ-7: how each failed submit is shown. */
  const failures: { why: string; failure: SetupFailure; message: string; lost: boolean }[] = [
    { why: '403 not_authenticated goes back to the token',
      failure: failure(403, 'not_authenticated', 'Enter the setup token first.'),
      message: '', lost: true },
    { why: 'a worker refusal is shown on its field only',
      failure: failure(400, 'invalid_key', 'The key is wrong.',
        { 'manager.enrollment_key': ['The key is wrong.'] }),
      message: '', lost: false },
    { why: '409 shows its message',
      failure: failure(409, 'conflict', 'Setup is already being saved, or was saved.'),
      message: 'Setup is already being saved, or was saved.', lost: false },
    { why: '500 shows its message',
      failure: failure(500, 'worker_failed', 'The worker program failed.'),
      message: 'The worker program failed.', lost: false },
  ];
  for (const c of failures) {
    it(`on submit failure: ${c.why}`, () => {
      api.submit.and.returnValue(throwError(() => c.failure));
      const lost = jasmine.createSpy('sessionLost');
      component.sessionLost.subscribe(lost);

      component.submit();

      expect(component.submitting).toBeFalse();
      expect(component.message).toBe(c.message);
      expect(api.clearSession).toHaveBeenCalledTimes(c.lost ? 1 : 0);
      expect(lost).toHaveBeenCalledTimes(c.lost ? 1 : 0);
      if (!c.lost) {
        expect(component.errors).toEqual(c.failure.fields);
      }
    });
  }

  /** FR-WZ-2: a 200 hands the finish answer to the page. */
  it('emits the finish answer on success', () => {
    const info = finishInfo();
    api.submit.and.returnValue(of(info));
    const finished = jasmine.createSpy('finished');
    component.finished.subscribe(finished);

    component.submit();

    expect(api.submit).toHaveBeenCalledOnceWith(form);
    expect(finished).toHaveBeenCalledOnceWith(info);
  });

  /** FR-WZ-6, FR-WZ-11: the "again" fields of the passwords this mode shows must match before sending. */
  const confirms: { why: string; edit: (f: SetupForm, c: SetupFormComponent) => void; message: string }[] = [
    { why: 'admin password differs', message: 'The admin passwords are not the same.',
      edit: (f, c) => { f.admin.password = 'secret-1'; c.confirm.admin = 'secret-2'; } },
    { why: 'admin kept: its password is not checked', message: '',
      edit: (f, c) => { f.admin.keep_existing = true; f.admin.password = 'x'; c.confirm.admin = ''; } },
    { why: 'worker page password differs', message: 'The worker page passwords are not the same.',
      edit: (f, c) => { f.mode = 'worker'; f.worker.ui_password = 'pw-1'; c.confirm.worker = 'pw-2'; } },
  ];
  for (const c of confirms) {
    it(`checks the password confirmations: ${c.why}`, () => {
      api.submit.and.returnValue(of(finishInfo()));
      c.edit(form, component);

      component.submit();

      expect(component.message).toBe(c.message);
      expect(api.submit).toHaveBeenCalledTimes(c.message ? 0 : 1);
    });
  }
});
