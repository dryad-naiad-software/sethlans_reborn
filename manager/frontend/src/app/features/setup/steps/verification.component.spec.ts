// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { VerificationComponent } from './verification.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

describe('VerificationComponent', () => {
  let component: VerificationComponent;
  let fixture: ComponentFixture<VerificationComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', ['verify']);

    await TestBed.configureTestingModule({
      imports: [VerificationComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
      ],
    }).compileComponents();

    state = TestBed.inject(SetupStateService);
  });

  /** Creates fixture, spies on snackBar, then runs detectChanges. */
  function createAndDetect() {
    fixture = TestBed.createComponent(VerificationComponent);
    component = fixture.componentInstance;
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
  }

  it('should create', () => {
    mockApi.verify.and.returnValue(of({ checks: [], all_passed: true }));
    createAndDetect();
    expect(component).toBeTruthy();
  });

  it('should show loading initially', () => {
    mockApi.verify.and.returnValue(of({ checks: [], all_passed: true }));
    fixture = TestBed.createComponent(VerificationComponent);
    component = fixture.componentInstance;
    expect(component.loading).toBeTrue();
  });

  describe('all checks pass', () => {
    beforeEach(() => {
      const checks = [
        { name: 'Database', passed: true, error: null },
        { name: 'Admin', passed: true, error: null },
      ];
      mockApi.verify.and.returnValue(of({ checks, all_passed: true }));
      createAndDetect();
    });

    it('should set allPassed to true', () => {
      expect(component.allPassed).toBeTrue();
    });

    it('should set loading to false', () => {
      expect(component.loading).toBeFalse();
    });

    it('should populate checks', () => {
      expect(component.checks.length).toBe(2);
    });

    it('should mark checkpoint', () => {
      expect(state.checkpoints).toContain('verified');
    });
  });

  describe('some checks fail', () => {
    beforeEach(() => {
      const checks = [
        { name: 'Database', passed: true, error: null },
        { name: 'Admin', passed: false, error: 'Missing' },
      ];
      mockApi.verify.and.returnValue(of({ checks, all_passed: false }));
      createAndDetect();
    });

    it('should set allPassed to false', () => {
      expect(component.allPassed).toBeFalse();
    });

    it('should not mark checkpoint', () => {
      expect(state.checkpoints).not.toContain('verified');
    });

    it('should display failed check with error', () => {
      const failedCheck = component.checks.find(c => !c.passed);
      expect(failedCheck).toBeDefined();
      expect(failedCheck!.error).toBe('Missing');
    });
  });

  describe('verification error', () => {
    it('should show snackbar on api error with detail', () => {
      mockApi.verify.and.returnValue(
        throwError(() => ({ error: { detail: 'Server error' } })));
      createAndDetect();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Server error', 'Dismiss', { duration: 5000 });
      expect(component.loading).toBeFalse();
    });

    it('should show fallback message when no detail', () => {
      mockApi.verify.and.returnValue(
        throwError(() => ({ error: {} })));
      createAndDetect();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Verification failed', 'Dismiss', { duration: 5000 });
    });
  });

  describe('runVerification (retry)', () => {
    it('should re-run verification', () => {
      mockApi.verify.and.returnValue(
        of({ checks: [], all_passed: false }));
      createAndDetect();

      mockApi.verify.and.returnValue(
        of({ checks: [], all_passed: true }));
      component.runVerification();
      expect(component.allPassed).toBeTrue();
      expect(mockApi.verify).toHaveBeenCalledTimes(2);
    });
  });
});
