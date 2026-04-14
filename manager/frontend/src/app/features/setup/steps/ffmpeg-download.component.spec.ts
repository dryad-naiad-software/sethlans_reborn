// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { FfmpegDownloadComponent } from './ffmpeg-download.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

describe('FfmpegDownloadComponent', () => {
  let component: FfmpegDownloadComponent;
  let fixture: ComponentFixture<FfmpegDownloadComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', [
      'startFfmpegDownload', 'getFfmpegProgress', 'cancelFfmpegDownload',
    ]);
    mockApi.cancelFfmpegDownload.and.returnValue(of({ status: 'cancelled' }));

    await TestBed.configureTestingModule({
      imports: [FfmpegDownloadComponent, NoopAnimationsModule],
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
      mockApi.startFfmpegDownload.and.returnValue(
        of({ status: 'already_installed' as const, task_id: null }));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      fixture.detectChanges();
      expect(state.checkpoints).toContain('ffmpeg_installed');
      expect(emitted).toBeTrue();
      expect(component.starting).toBeFalse();
    });
  });

  describe('download started with polling', () => {
    it('should poll and mark checkpoint on complete', fakeAsync(() => {
      mockApi.startFfmpegDownload.and.returnValue(
        of({ status: 'started' as const, task_id: 'task-ffmpeg' }));
      mockApi.getFfmpegProgress.and.returnValue(
        of({ status: 'complete' as const, percent: 100, error: null }));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      tick(1500); // first poll interval
      expect(mockApi.getFfmpegProgress).toHaveBeenCalledWith('task-ffmpeg');
      expect(state.checkpoints).toContain('ffmpeg_installed');
      component.ngOnDestroy();
    }));

    it('should set failed state when poll returns failed', fakeAsync(() => {
      mockApi.startFfmpegDownload.and.returnValue(
        of({ status: 'started' as const, task_id: 'task-ffmpeg' }));
      mockApi.getFfmpegProgress.and.returnValue(
        of({ status: 'failed' as const, percent: 0, error: 'Checksum mismatch' }));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      tick(1500);
      expect(component.failed).toBeTrue();
      expect(component.errorText).toBe('Checksum mismatch');
      component.ngOnDestroy();
    }));
  });

  describe('download start error', () => {
    it('should show error state with detail', () => {
      mockApi.startFfmpegDownload.and.returnValue(
        throwError(() => ({ error: { detail: 'Platform not supported' } })));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.failed).toBeTrue();
      expect(component.errorText).toBe('Platform not supported');
      expect(component.starting).toBeFalse();
    });

    it('should show fallback error text', () => {
      mockApi.startFfmpegDownload.and.returnValue(
        throwError(() => ({ error: {} })));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.errorText).toBe('Failed to start FFmpeg download');
    });
  });

  describe('cancel', () => {
    it('should call cancel api and show cancelled state', fakeAsync(() => {
      mockApi.startFfmpegDownload.and.returnValue(
        of({ status: 'started' as const, task_id: 'task-1' }));
      mockApi.getFfmpegProgress.and.returnValue(
        of({ status: 'downloading' as const, percent: 50, error: null }));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      tick(1500);
      component.cancel();
      expect(mockApi.cancelFfmpegDownload).toHaveBeenCalled();
      expect(component.failed).toBeTrue();
      expect(component.errorText).toBe('Download cancelled');
      expect(component.progress).toBeNull();
    }));
  });

  describe('retry', () => {
    it('should reset state and restart download', () => {
      mockApi.startFfmpegDownload.and.returnValue(
        throwError(() => ({ error: {} })));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.failed).toBeTrue();

      mockApi.startFfmpegDownload.and.returnValue(
        of({ status: 'already_installed' as const, task_id: null }));
      component.retry();
      expect(component.failed).toBeFalse();
    });
  });

  describe('statusLabel', () => {
    beforeEach(() => {
      mockApi.startFfmpegDownload.and.returnValue(
        of({ status: 'already_installed' as const, task_id: null }));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
    });

    it('should return empty string when no progress', () => {
      component.progress = null;
      expect(component.statusLabel).toBe('');
    });

    it('should return correct label for downloading', () => {
      component.progress = { status: 'downloading', percent: 50, error: null };
      expect(component.statusLabel).toBe('Downloading FFmpeg...');
    });

    it('should return correct label for extracting', () => {
      component.progress = { status: 'extracting', percent: 0, error: null };
      expect(component.statusLabel).toBe('Extracting...');
    });
  });

  describe('ngOnDestroy', () => {
    it('should not throw when destroyed', () => {
      mockApi.startFfmpegDownload.and.returnValue(
        of({ status: 'already_installed' as const, task_id: null }));
      fixture = TestBed.createComponent(FfmpegDownloadComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(() => component.ngOnDestroy()).not.toThrow();
    });
  });
});
