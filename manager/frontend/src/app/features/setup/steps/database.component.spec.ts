// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { DatabaseComponent } from './database.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

describe('DatabaseComponent', () => {
  let component: DatabaseComponent;
  let fixture: ComponentFixture<DatabaseComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', [
      'configureDatabase', 'getStatus',
    ]);
    mockApi.configureDatabase.and.returnValue(of({ status: 'ok' as const }));

    await TestBed.configureTestingModule({
      imports: [DatabaseComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(DatabaseComponent);
    component = fixture.componentInstance;
    state = TestBed.inject(SetupStateService);
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should default to sqlite engine', () => {
    expect(component.selectedEngine).toBe('sqlite');
  });

  describe('form validation', () => {
    it('should be valid with sqlite defaults', () => {
      expect(component.form.valid).toBeTrue();
    });

    it('should require engine', () => {
      component.form.get('engine')!.setValue(null);
      expect(component.form.get('engine')!.hasError('required')).toBeTrue();
    });
  });

  describe('onSubmit with sqlite', () => {
    it('should call api with sqlite engine only', () => {
      component.onSubmit();
      expect(mockApi.configureDatabase).toHaveBeenCalledWith({ engine: 'sqlite' });
    });

    it('should mark checkpoint on success', () => {
      component.onSubmit();
      expect(state.checkpoints).toContain('database_configured');
    });

    it('should emit stepComplete when status is ok', () => {
      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      component.onSubmit();
      expect(emitted).toBeTrue();
    });
  });

  describe('onSubmit with postgresql', () => {
    beforeEach(() => {
      component.form.patchValue({
        engine: 'postgresql',
        host: 'db.local', port: '5432',
        name: 'sethlans', user: 'admin', password: 'secret',
      });
    });

    it('should include connection fields for non-sqlite engine', () => {
      component.onSubmit();
      expect(mockApi.configureDatabase).toHaveBeenCalledWith({
        engine: 'postgresql',
        host: 'db.local', port: '5432',
        name: 'sethlans', user: 'admin', password: 'secret',
      });
    });
  });

  describe('onSubmit with custom engine', () => {
    it('should include engine_path for custom engine', () => {
      component.form.patchValue({
        engine: 'custom',
        engine_path: 'django.db.backends.oracle',
        host: 'ora.local', port: '1521',
        name: 'orcl', user: 'sys', password: 'pass',
      });
      component.onSubmit();
      const call = mockApi.configureDatabase.calls.mostRecent().args[0];
      expect(call.engine_path).toBe('django.db.backends.oracle');
    });
  });

  describe('restart_required flow', () => {
    it('should set waitingForRestart when status is restart_required', () => {
      mockApi.configureDatabase.and.returnValue(
        of({ status: 'restart_required' as const }));
      mockApi.getStatus.and.returnValue(of({
        complete: false, topology: null,
        current_step: null, checkpoints: ['database_configured'],
      }));
      component.onSubmit();
      // waitingForRestart is set before polling resolves
      expect(component.waitingForRestart).toBeTrue();
    });
  });

  describe('error handling', () => {
    it('should show snackbar on error', () => {
      mockApi.configureDatabase.and.returnValue(
        throwError(() => ({ error: { detail: 'Connection failed' } })));
      component.onSubmit();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Connection failed', 'Dismiss', { duration: 5000 });
    });

    it('should show fallback message when no detail', () => {
      mockApi.configureDatabase.and.returnValue(
        throwError(() => ({ error: {} })));
      component.onSubmit();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to configure database', 'Dismiss', { duration: 5000 });
    });

    it('should reset submitting on error', () => {
      mockApi.configureDatabase.and.returnValue(
        throwError(() => ({ error: {} })));
      component.onSubmit();
      expect(component.submitting).toBeFalse();
    });
  });

  describe('ngOnDestroy', () => {
    it('should not throw when no poll subscription exists', () => {
      expect(() => component.ngOnDestroy()).not.toThrow();
    });
  });
});
