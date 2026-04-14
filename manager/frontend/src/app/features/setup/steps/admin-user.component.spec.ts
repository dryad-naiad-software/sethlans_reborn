// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { HttpErrorResponse } from '@angular/common/http';
import { of, throwError } from 'rxjs';
import { AdminUserComponent } from './admin-user.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

describe('AdminUserComponent', () => {
  let component: AdminUserComponent;
  let fixture: ComponentFixture<AdminUserComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', ['createAdminUser']);
    mockApi.createAdminUser.and.returnValue(
      of({ status: 'ok', username: 'admin' }));

    await TestBed.configureTestingModule({
      imports: [AdminUserComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AdminUserComponent);
    component = fixture.componentInstance;
    state = TestBed.inject(SetupStateService);
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('form validation', () => {
    it('should be invalid initially', () => {
      expect(component.form.invalid).toBeTrue();
    });

    it('should require username', () => {
      expect(component.form.get('username')!.hasError('required')).toBeTrue();
    });

    it('should require email', () => {
      expect(component.form.get('email')!.hasError('required')).toBeTrue();
    });

    it('should validate email format', () => {
      component.form.patchValue({ email: 'not-an-email' });
      expect(component.form.get('email')!.hasError('email')).toBeTrue();
    });

    it('should require password', () => {
      expect(component.form.get('password')!.hasError('required')).toBeTrue();
    });

    it('should require password minimum 8 characters', () => {
      component.form.patchValue({ password: 'short' });
      expect(component.form.get('password')!.hasError('minlength')).toBeTrue();
    });

    it('should require password_confirm', () => {
      expect(component.form.get('password_confirm')!.hasError('required')).toBeTrue();
    });

    it('should detect password mismatch', () => {
      component.form.patchValue({
        username: 'admin', email: 'a@b.com',
        password: 'password1', password_confirm: 'password2',
      });
      expect(component.form.hasError('passwordMismatch')).toBeTrue();
    });

    it('should be valid when all fields correct and passwords match', () => {
      component.form.patchValue({
        username: 'admin', email: 'a@b.com',
        password: 'password1', password_confirm: 'password1',
      });
      expect(component.form.valid).toBeTrue();
    });
  });

  describe('onSubmit', () => {
    beforeEach(() => {
      component.form.patchValue({
        username: 'admin', email: 'a@b.com',
        password: 'password1', password_confirm: 'password1',
      });
    });

    it('should not submit when form is invalid', () => {
      component.form.patchValue({ username: '' });
      component.onSubmit();
      expect(mockApi.createAdminUser).not.toHaveBeenCalled();
    });

    it('should call api with form values', () => {
      component.onSubmit();
      expect(mockApi.createAdminUser).toHaveBeenCalledWith({
        username: 'admin', email: 'a@b.com',
        password: 'password1', password_confirm: 'password1',
      });
    });

    it('should store admin password in state on success', () => {
      component.onSubmit();
      expect(state.getAdminPassword()).toBe('password1');
    });

    it('should mark checkpoint on success', () => {
      component.onSubmit();
      expect(state.checkpoints).toContain('admin_created');
    });

    it('should emit stepComplete on success', () => {
      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      component.onSubmit();
      expect(emitted).toBeTrue();
    });

    it('should show conflict error for HTTP 409', () => {
      mockApi.createAdminUser.and.returnValue(
        throwError(() => new HttpErrorResponse({ status: 409 })));
      component.onSubmit();
      expect(component.errorMessage).toBe('An admin account already exists.');
      expect(component.adminExists).toBeTrue();
    });

    it('should show detail error for other errors', () => {
      mockApi.createAdminUser.and.returnValue(
        throwError(() => new HttpErrorResponse({
          status: 400, error: { detail: 'Password too common' },
        })));
      component.onSubmit();
      expect(component.errorMessage).toBe('Password too common');
    });

    it('should show fallback error when no detail', () => {
      mockApi.createAdminUser.and.returnValue(
        throwError(() => new HttpErrorResponse({ status: 500 })));
      component.onSubmit();
      expect(component.errorMessage).toBe('Failed to create admin account');
    });

    it('should reset submitting on error', () => {
      mockApi.createAdminUser.and.returnValue(
        throwError(() => new HttpErrorResponse({ status: 500 })));
      component.onSubmit();
      expect(component.submitting).toBeFalse();
    });
  });

  describe('continueWithExisting', () => {
    it('should mark checkpoint and emit stepComplete', () => {
      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      component.continueWithExisting();
      expect(state.checkpoints).toContain('admin_created');
      expect(emitted).toBeTrue();
    });
  });
});
