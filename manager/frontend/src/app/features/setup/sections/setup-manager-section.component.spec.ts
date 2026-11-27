// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { discoveredManager, setupForm } from '../setup.fixtures';
import { SetupEnrollment, SetupForm } from '../setup.types';
import { SetupManagerSectionComponent } from './setup-manager-section.component';

const ID = 'a1b2c3d4-0000-4000-8000-000000000001';

function enrollment(overrides: Partial<SetupEnrollment> = {}): SetupEnrollment {
  return { manager_id: ID, manager_name: null, host: null, port: null, ...overrides };
}

describe('SetupManagerSectionComponent', () => {
  let fixture: ComponentFixture<SetupManagerSectionComponent>;
  let form: SetupForm;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SetupManagerSectionComponent, NoopAnimationsModule],
    }).compileComponents();
    fixture = TestBed.createComponent(SetupManagerSectionComponent);
    form = setupForm();
    form.mode = 'worker';
  });

  function render(): string {
    fixture.componentRef.setInput('form', form);
    fixture.detectChanges();
    return fixture.nativeElement.textContent;
  }

  /** FR-RS-3: an enrolled worker is offered "Keep enrollment with <manager>", which hides URL and key. */
  it('offers keeping the enrollment only when the worker is enrolled', () => {
    expect(render()).not.toContain('Keep enrollment with');
    expect(render()).toContain('Enrollment key');

    form.enrollment = enrollment({ manager_name: 'Studio manager' });
    form.manager.keep_enrollment = true;
    const text = render();
    expect(text).toContain('Keep enrollment with Studio manager');
    expect(text).not.toContain('Enrollment key');
  });

  /**
   * FR-RS-3: the manager in "Keep enrollment with ..." is named by the
   * worker's saved name, then a manager found now with the same id, then
   * host:port, then the manager id.
   */
  const names: { why: string; enrollment: SetupEnrollment; expected: string }[] = [
    { why: 'saved name before a found name',
      enrollment: enrollment({ manager_name: 'Saved name', host: 'studio.local', port: 8080 }),
      expected: 'Saved name (studio.local:8080)' },
    { why: 'found manager with the same id',
      enrollment: enrollment({ host: 'studio.local', port: 8080 }),
      expected: 'Found name (studio.local:8080)' },
    { why: 'host and port', enrollment: enrollment({ manager_id: 'other-id', host: 'studio.local', port: 8443 }),
      expected: 'studio.local:8443' },
    { why: 'host without port', enrollment: enrollment({ manager_id: 'other-id', host: 'studio.local' }),
      expected: 'studio.local' },
    { why: 'manager id only', enrollment: enrollment({ manager_id: 'other-id' }), expected: 'other-id' },
    { why: 'nothing known', enrollment: enrollment({ manager_id: null }), expected: 'the current manager' },
  ];
  for (const c of names) {
    it(`names the enrolled manager: ${c.why}`, () => {
      form.managers = [discoveredManager({ name: 'Found name', manager_id: ID })];
      form.enrollment = c.enrollment;
      fixture.componentRef.setInput('form', form);
      expect(fixture.componentInstance.enrolledName()).toBe(c.expected);
    });
  }

  /** FR-WZ-3, FR-WZ-5: picking a found manager fills its URL and name; a typed URL has no name. */
  it('fills URL and name from a picked manager and clears the name for a typed URL', () => {
    form.managers = [
      discoveredManager({ name: 'Unreachable', url: null, manager_id: 'x' }),
      discoveredManager(),
    ];
    render();
    const radios: HTMLInputElement[] = Array.from(fixture.nativeElement.querySelectorAll('mat-radio-button input'));
    expect(radios.map(radio => radio.disabled)).toEqual([true, false]);
    // Security review N4: each found manager shows the address it would be contacted at.
    const labels = Array.from(fixture.nativeElement.querySelectorAll('mat-radio-button'))
      .map(node => (node as HTMLElement).textContent!);
    expect(labels[0]).toContain('no address to contact');
    expect(labels[1]).toContain('https://studio.local:8080');

    radios[1].click();
    fixture.detectChanges();
    expect(form.manager.url).toBe('https://studio.local:8080');
    expect(form.manager.name).toBe('Studio manager');

    const address = fixture.nativeElement.querySelector('input.mat-mdc-input-element') as HTMLInputElement;
    address.value = 'https://other.local:8080';
    address.dispatchEvent(new Event('input'));
    expect(form.manager.url).toBe('https://other.local:8080');
    expect(form.manager.name).toBe('');
  });
});
