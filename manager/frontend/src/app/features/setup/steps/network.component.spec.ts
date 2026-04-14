// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { NetworkComponent } from './network.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

describe('NetworkComponent', () => {
  let component: NetworkComponent;
  let fixture: ComponentFixture<NetworkComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', ['configureNetwork']);
    mockApi.configureNetwork.and.returnValue(
      of({ status: 'ok', bind_host: '0.0.0.0', bind_port: 8080 }));

    await TestBed.configureTestingModule({
      imports: [NetworkComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(NetworkComponent);
    component = fixture.componentInstance;
    state = TestBed.inject(SetupStateService);
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('form defaults', () => {
    it('should have bind_host default to 0.0.0.0', () => {
      expect(component.form.value.bind_host).toBe('0.0.0.0');
    });

    it('should have bind_port default to 8080', () => {
      expect(component.form.value.bind_port).toBe(8080);
    });

    it('should have empty data_dir', () => {
      expect(component.form.value.data_dir).toBe('');
    });
  });

  describe('form validation', () => {
    it('should be valid with defaults', () => {
      expect(component.form.valid).toBeTrue();
    });

    it('should allow empty bind_host (derived from toggle)', () => {
      component.form.patchValue({ bind_host: '' });
      expect(component.form.get('bind_host')!.valid).toBeTrue();
    });

    it('should require bind_port', () => {
      component.form.patchValue({ bind_port: null });
      expect(component.form.get('bind_port')!.hasError('required')).toBeTrue();
    });

    it('should reject port less than 1', () => {
      component.form.patchValue({ bind_port: 0 });
      expect(component.form.get('bind_port')!.hasError('min')).toBeTrue();
    });

    it('should reject port greater than 65535', () => {
      component.form.patchValue({ bind_port: 70000 });
      expect(component.form.get('bind_port')!.hasError('max')).toBeTrue();
    });
  });

  describe('allow_remote toggle', () => {
    it('should default to true', () => {
      expect(component.form.value.allow_remote).toBeTrue();
    });

    it('should use 127.0.0.1 when disabled and bind_host empty', () => {
      component.form.patchValue({ allow_remote: false, bind_host: '' });
      component.onSubmit();
      expect(mockApi.configureNetwork).toHaveBeenCalledWith(
        jasmine.objectContaining({ bind_host: '127.0.0.1' }));
    });

    it('should use 0.0.0.0 when enabled and bind_host empty', () => {
      component.form.patchValue({ allow_remote: true, bind_host: '' });
      component.onSubmit();
      expect(mockApi.configureNetwork).toHaveBeenCalledWith(
        jasmine.objectContaining({ bind_host: '0.0.0.0' }));
    });
  });

  describe('onSubmit', () => {
    it('should not submit when form is invalid', () => {
      component.form.patchValue({ bind_port: null });
      component.onSubmit();
      expect(mockApi.configureNetwork).not.toHaveBeenCalled();
    });

    it('should call api with form values', () => {
      component.onSubmit();
      expect(mockApi.configureNetwork).toHaveBeenCalledWith({
        bind_host: '0.0.0.0', bind_port: 8080,
      });
    });

    it('should include data_dir when set', () => {
      component.form.patchValue({ data_dir: '/custom/path' });
      component.onSubmit();
      expect(mockApi.configureNetwork).toHaveBeenCalledWith({
        bind_host: '0.0.0.0', bind_port: 8080, data_dir: '/custom/path',
      });
    });

    it('should mark checkpoint on success', () => {
      component.onSubmit();
      expect(state.checkpoints).toContain('network_configured');
    });

    it('should emit stepComplete on success', () => {
      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      component.onSubmit();
      expect(emitted).toBeTrue();
    });

    it('should show snackbar on error', () => {
      mockApi.configureNetwork.and.returnValue(
        throwError(() => ({ error: { detail: 'Port in use' } })));
      component.onSubmit();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Port in use', 'Dismiss', { duration: 5000 });
    });

    it('should reset submitting on error', () => {
      mockApi.configureNetwork.and.returnValue(
        throwError(() => ({ error: {} })));
      component.onSubmit();
      expect(component.submitting).toBeFalse();
    });

    it('should not double-submit when already submitting', () => {
      component.submitting = true;
      component.onSubmit();
      expect(mockApi.configureNetwork).not.toHaveBeenCalled();
    });
  });
});
