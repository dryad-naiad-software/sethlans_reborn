// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { BlenderDownloadComponent } from './blender-download.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

describe('BlenderDownloadComponent', () => {
  let component: BlenderDownloadComponent;
  let fixture: ComponentFixture<BlenderDownloadComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', [
      'startBlenderDownload', 'getBlenderProgress', 'cancelBlenderDownload',
    ]);
    mockApi.cancelBlenderDownload.and.returnValue(of({ status: 'cancelled' }));

    await TestBed.configureTestingModule({
      imports: [BlenderDownloadComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
      ],
    }).compileComponents();

    state = TestBed.inject(SetupStateService);
    snackBar = TestBed.inject(MatSnackBar);
    spyOn(snackBar, 'open');
  });

  describe('already installed', () => {
    it('should mark checkpoint and emit stepComplete', () => {
      mockApi.startBlenderDownload.and.returnValue(
        of({
          status: 'already_installed' as const,
          task_id: null, version: '4.2.1',
        }));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      fixture.detectChanges();
      expect(state.checkpoints).toContain('blender_predownloaded');
      expect(emitted).toBeTrue();
      expect(component.version).toBe('4.2.1');
      expect(component.starting).toBeFalse();
    });
  });

  describe('download started with polling', () => {
    it('should poll and mark checkpoint on complete', fakeAsync(() => {
      mockApi.startBlenderDownload.and.returnValue(
        of({
          status: 'started' as const,
          task_id: 'task-blender', version: '4.2.1',
        }));
      mockApi.getBlenderProgress.and.returnValue(
        of({ status: 'complete' as const, percent: 100, error: null }));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      tick(1500);
      expect(mockApi.getBlenderProgress).toHaveBeenCalledWith('task-blender');
      expect(state.checkpoints).toContain('blender_predownloaded');
      component.ngOnDestroy();
    }));

    it('should set failed state when poll returns failed', fakeAsync(() => {
      mockApi.startBlenderDownload.and.returnValue(
        of({
          status: 'started' as const,
          task_id: 'task-blender', version: '4.2.1',
        }));
      mockApi.getBlenderProgress.and.returnValue(
        of({ status: 'failed' as const, percent: 0, error: 'Network error' }));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      tick(1500);
      expect(component.failed).toBeTrue();
      expect(component.errorText).toBe('Network error');
      component.ngOnDestroy();
    }));
  });

  describe('download start error', () => {
    it('should show error state with detail', () => {
      mockApi.startBlenderDownload.and.returnValue(
        throwError(() => ({ error: { detail: 'Version not found' } })));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.failed).toBeTrue();
      expect(component.errorText).toBe('Version not found');
    });

    it('should show fallback error text', () => {
      mockApi.startBlenderDownload.and.returnValue(
        throwError(() => ({ error: {} })));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.errorText).toBe('Failed to start Blender download');
    });
  });

  describe('cancel', () => {
    it('should call cancel api and set failed state', fakeAsync(() => {
      mockApi.startBlenderDownload.and.returnValue(
        of({
          status: 'started' as const,
          task_id: 'task-1', version: '4.2.1',
        }));
      mockApi.getBlenderProgress.and.returnValue(
        of({ status: 'downloading' as const, percent: 30, error: null }));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      tick(1500);
      component.cancel();
      expect(mockApi.cancelBlenderDownload).toHaveBeenCalled();
      expect(component.failed).toBeTrue();
      expect(component.errorText).toBe('Download cancelled');
    }));
  });

  describe('skip', () => {
    it('should emit stepComplete without downloading', () => {
      mockApi.startBlenderDownload.and.returnValue(
        throwError(() => ({ error: {} })));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();

      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      component.skip();
      expect(emitted).toBeTrue();
    });
  });

  describe('retry', () => {
    it('should reset error state and restart download', () => {
      mockApi.startBlenderDownload.and.returnValue(
        throwError(() => ({ error: {} })));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.failed).toBeTrue();

      mockApi.startBlenderDownload.and.returnValue(
        of({
          status: 'already_installed' as const,
          task_id: null, version: '4.2.1',
        }));
      component.retry();
      expect(component.failed).toBeFalse();
    });
  });

  describe('statusLabel', () => {
    beforeEach(() => {
      mockApi.startBlenderDownload.and.returnValue(
        of({
          status: 'already_installed' as const,
          task_id: null, version: '4.2.1',
        }));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
    });

    it('should return empty string when no progress', () => {
      component.progress = null;
      expect(component.statusLabel).toBe('');
    });

    it('should return correct label for downloading', () => {
      component.progress = { status: 'downloading', percent: 50, error: null };
      expect(component.statusLabel).toBe('Downloading Blender...');
    });
  });

  describe('ngOnDestroy', () => {
    it('should not throw when destroyed', () => {
      mockApi.startBlenderDownload.and.returnValue(
        of({
          status: 'already_installed' as const,
          task_id: null, version: '4.2.1',
        }));
      fixture = TestBed.createComponent(BlenderDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(() => component.ngOnDestroy()).not.toThrow();
    });
  });
});
