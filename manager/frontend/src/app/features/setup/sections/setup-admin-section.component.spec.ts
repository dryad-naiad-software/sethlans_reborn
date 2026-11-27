// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { setupForm } from '../setup.fixtures';
import { FieldCodes, SetupForm } from '../setup.types';
import { SetupAdminSectionComponent } from './setup-admin-section.component';

describe('SetupAdminSectionComponent', () => {
  let fixture: ComponentFixture<SetupAdminSectionComponent>;
  let form: SetupForm;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SetupAdminSectionComponent, NoopAnimationsModule],
    }).compileComponents();
    fixture = TestBed.createComponent(SetupAdminSectionComponent);
    form = setupForm();
  });

  function render(codes: FieldCodes = {}): string {
    fixture.componentRef.setInput('form', form);
    fixture.componentRef.setInput('confirm', { admin: '', worker: '' });
    fixture.componentRef.setInput('codes', codes);
    fixture.detectChanges();
    return fixture.nativeElement.textContent;
  }

  /** FR-WZ-11: with admins in the database, "Keep the existing admin accounts" hides the new account. */
  it('offers keeping the existing admin accounts only when there are some', () => {
    expect(render()).not.toContain('Keep the existing admin accounts');
    expect(render()).toContain('Username');

    form.admin.accounts_exist = true;
    form.admin.keep_existing = true;
    const text = render();
    expect(text).toContain('Keep the existing admin accounts');
    expect(text).not.toContain('Username');
  });

  /** Review N3: keeping the existing admins replaces no password. */
  it('clears replace_password when keep_existing is ticked', () => {
    form.admin.accounts_exist = true;
    form.admin.replace_password = true;
    render();

    (fixture.nativeElement.querySelector('mat-checkbox input') as HTMLInputElement).click();

    expect(form.admin.keep_existing).toBeTrue();
    expect(form.admin.replace_password).toBeFalse();
  });

  /** FR-WZ-11, OQ-2: "Replace its password?" once the server's code says the account exists, or when set. */
  const offers: { why: string; codes: FieldCodes; replace: boolean; offered: boolean }[] = [
    { why: 'no code', codes: {}, replace: false, offered: false },
    { why: 'another username code', codes: { 'admin.username': ['invalid'] },
      replace: false, offered: false },
    { why: 'the server says the account exists',
      codes: { 'admin.username': ['account_exists'] }, replace: false, offered: true },
    { why: 'the tick is already set', codes: {}, replace: true, offered: true },
  ];
  for (const c of offers) {
    it(`offers replacing the password: ${c.why}`, () => {
      form.admin.username = 'admin';
      form.admin.replace_password = c.replace;
      render(c.codes);
      expect(fixture.componentInstance.offerReplace).toBe(c.offered);
      const boxes = Array.from(fixture.nativeElement.querySelectorAll('mat-checkbox')) as HTMLElement[];
      expect(boxes.some(box => box.textContent!.includes('Replace its password?'))).toBe(c.offered);
    });
  }
});
