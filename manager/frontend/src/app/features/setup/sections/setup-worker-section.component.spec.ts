// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { setupForm } from '../setup.fixtures';
import { SetupWorkerSectionComponent } from './setup-worker-section.component';

describe('SetupWorkerSectionComponent', () => {
  /** FR-WZ-1, OQ-4: the worker page password is never sent back; an empty field keeps it. */
  const cases: { set: boolean; placeholder: string }[] = [
    { set: true, placeholder: 'Empty: keep the current password' },
    { set: false, placeholder: '' },
  ];
  for (const c of cases) {
    it(`says what an empty worker page password does (stored: ${c.set})`, async () => {
      await TestBed.configureTestingModule({
        imports: [SetupWorkerSectionComponent, NoopAnimationsModule],
      }).compileComponents();
      const fixture = TestBed.createComponent(SetupWorkerSectionComponent);
      const form = setupForm();
      form.worker.ui_password_set = c.set;
      fixture.componentRef.setInput('form', form);
      fixture.componentRef.setInput('confirm', { admin: '', worker: '' });
      fixture.detectChanges();

      const password = fixture.nativeElement.querySelector('input[type=password]') as HTMLInputElement;
      expect(password.value).toBe('');
      expect(password.placeholder).toBe(c.placeholder);
    });
  }
});
