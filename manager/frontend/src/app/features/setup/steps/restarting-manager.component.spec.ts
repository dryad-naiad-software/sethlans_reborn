// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { RestartingManagerComponent } from './restarting-manager.component';
import { SetupSummary } from '../models/setup.models';

const MOCK_SUMMARY: SetupSummary = {
  manager_url: 'https://localhost:8080',
  admin_username: 'admin',
  enrollment_key: 'ABC123',
  cert_fingerprint: 'AA:BB:CC:DD',
  topology: 'manager',
};

describe('RestartingManagerComponent', () => {
  let fixture: ComponentFixture<RestartingManagerComponent>;
  let component: RestartingManagerComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RestartingManagerComponent, NoopAnimationsModule],
    }).compileComponents();
    fixture = TestBed.createComponent(RestartingManagerComponent);
    component = fixture.componentInstance;
  });

  it('is created', () => {
    component.phase = 'restarting';
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('restarting phase', () => {
    beforeEach(() => {
      component.phase = 'restarting';
      fixture.detectChanges();
    });

    it('shows mat-progress-spinner', () => {
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('mat-spinner')).not.toBeNull();
    });

    it('does not show retry button while restarting', () => {
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('button[mat-raised-button]')).toBeNull();
    });

    it('renders the status message input', () => {
      component.statusMessage = 'Custom waiting text';
      fixture.detectChanges();
      const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
      expect(text).toContain('Custom waiting text');
    });
  });

  describe('error phase', () => {
    beforeEach(() => {
      component.phase = 'error';
      component.summary = MOCK_SUMMARY;
      fixture.detectChanges();
    });

    it('shows error title', () => {
      const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
      expect(text).toContain('Restart timed out');
    });

    it('renders summary URL and fingerprint when provided', () => {
      const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
      expect(text).toContain('https://localhost:8080');
      expect(text).toContain('AA:BB:CC:DD');
    });

    it('hides spinner', () => {
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('mat-spinner')).toBeNull();
    });

    it('emits retryClick when retry button clicked', () => {
      const emissions: void[] = [];
      component.retryClick.subscribe(() => emissions.push(undefined));
      const button: HTMLButtonElement | null =
        (fixture.nativeElement as HTMLElement).querySelector(
          'button[mat-raised-button]',
        );
      expect(button).not.toBeNull();
      button!.click();
      expect(emissions.length).toBe(1);
    });

    it('renders without summary (optional input)', () => {
      component.summary = null;
      fixture.detectChanges();
      const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
      expect(text).toContain('Restart timed out');
      expect(text).not.toContain('https://localhost:8080');
    });
  });

  describe('not a MatDialog', () => {
    it('host is not inside a mat-dialog-container', () => {
      component.phase = 'restarting';
      fixture.detectChanges();
      const host: HTMLElement = fixture.nativeElement;
      expect(host.closest('mat-dialog-container')).toBeNull();
    });
  });
});
