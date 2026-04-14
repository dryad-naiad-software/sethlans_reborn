// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { WorkerPasswordComponent } from './worker-password.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

describe('WorkerPasswordComponent', () => {
  let component: WorkerPasswordComponent;
  let fixture: ComponentFixture<WorkerPasswordComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', ['setWorkerPassword']);
    mockApi.setWorkerPassword.and.returnValue(of({ status: 'ok' }));

    await TestBed.configureTestingModule({
      imports: [WorkerPasswordComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
      ],
    }).compileComponents();

    state = TestBed.inject(SetupStateService);
    state.setAdminPassword('adminpass1');

    fixture = TestBed.createComponent(WorkerPasswordComponent);
    component = fixture.componentInstance;
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should default useSameAsAdmin to true', () => {
    expect(component.useSameAsAdmin).toBeTrue();
  });

  it('should pre-fill password from admin password on init', () => {
    expect(component.form.value.password).toBe('adminpass1');
  });

  describe('form validation', () => {
    it('should require password', () => {
      component.form.patchValue({ password: '' });
      expect(component.form.get('password')!.hasError('required')).toBeTrue();
    });

    it('should require minimum 8 characters', () => {
      component.form.patchValue({ password: 'short' });
      expect(component.form.get('password')!.hasError('minlength')).toBeTrue();
    });

    it('should be valid with adequate password', () => {
      component.form.patchValue({ password: 'longpassword' });
      expect(component.form.valid).toBeTrue();
    });
  });

  describe('onToggleSamePassword', () => {
    it('should clear password when unchecked', () => {
      component.onToggleSamePassword(false);
      expect(component.form.value.password).toBe('');
      expect(component.useSameAsAdmin).toBeFalse();
    });

    it('should restore admin password when checked', () => {
      component.onToggleSamePassword(false);
      component.onToggleSamePassword(true);
      expect(component.form.value.password).toBe('adminpass1');
      expect(component.useSameAsAdmin).toBeTrue();
    });
  });

  describe('onSubmit', () => {
    it('should not submit when form is invalid', () => {
      component.form.patchValue({ password: '' });
      component.onSubmit();
      expect(mockApi.setWorkerPassword).not.toHaveBeenCalled();
    });

    it('should call api with password', () => {
      component.onSubmit();
      expect(mockApi.setWorkerPassword).toHaveBeenCalledWith({
        password: 'adminpass1',
      });
    });

    it('should mark checkpoint on success', () => {
      component.onSubmit();
      expect(state.checkpoints).toContain('worker_password_set');
    });

    it('should emit stepComplete on success', () => {
      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      component.onSubmit();
      expect(emitted).toBeTrue();
    });

    it('should show snackbar on error', () => {
      mockApi.setWorkerPassword.and.returnValue(
        throwError(() => ({ error: { detail: 'Weak password' } })));
      component.onSubmit();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Weak password', 'Dismiss', { duration: 5000 });
    });

    it('should reset submitting on error', () => {
      mockApi.setWorkerPassword.and.returnValue(
        throwError(() => ({ error: {} })));
      component.onSubmit();
      expect(component.submitting).toBeFalse();
    });
  });
});
