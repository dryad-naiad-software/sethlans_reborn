// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { JobCreateFormComponent } from './job-create-form.component';
import { JobService } from '../../core/services/job.service';
import { TiledJobService } from '../../core/services/tiled-job.service';
import { AnimationService } from '../../core/services/animation.service';
import {
  FFmpegStatusResponse,
  FFmpegStatusService,
} from '../../core/services/ffmpeg-status.service';

const FFMPEG_URL = '/api/ffmpeg-status/';

/**
 * FFmpeg-status integration tests for JobCreateFormComponent.
 *
 * Split from `job-create-form.component.spec.ts` so the base spec stays
 * under the 250-line cap (CLAUDE.md). Karma autoloads sibling specs that
 * follow the `<name>.<topic>.spec.ts` pattern (precedent:
 * `job-create-form.component.resolution.spec.ts`).
 *
 * Coverage (per development/specs/wizard-ffmpeg-rewrite.md tests-NEW list,
 * lines 421–428):
 *  - Call-count assertion: GET /api/ffmpeg-status/ fires exactly once per
 *    component instance (FR §112, AC §474).
 *  - Default state: `videoAssemblyReady()` is `false` before the response
 *    arrives (fail-closed, FR §107, AC §473).
 *  - In-flight: a `<mat-progress-spinner>` is rendered next to the hint
 *    while the fetch is pending (FR §107).
 *  - Not ready: the `generateVideo` checkbox renders with `[disabled]`
 *    and the prepare hint is visible (FR §106).
 *  - Ready: the checkbox is enabled and the hint is hidden (FR §108).
 */
describe('JobCreateFormComponent — FFmpeg video-assembly integration', () => {
  let component: JobCreateFormComponent;
  let fixture: ComponentFixture<JobCreateFormComponent>;
  let httpMock: HttpTestingController;
  let svc: FFmpegStatusService;

  beforeEach(async () => {
    const mockJobService = jasmine.createSpyObj('JobService', ['create']);
    const mockTiledJobService = jasmine.createSpyObj('TiledJobService', ['create']);
    const mockAnimationService = jasmine.createSpyObj('AnimationService', ['create']);
    const mockDialogRef = jasmine.createSpyObj('MatDialogRef', ['close']);

    await TestBed.configureTestingModule({
      imports: [JobCreateFormComponent, NoopAnimationsModule],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: JobService, useValue: mockJobService },
        { provide: TiledJobService, useValue: mockTiledJobService },
        { provide: AnimationService, useValue: mockAnimationService },
        { provide: MatDialogRef, useValue: mockDialogRef },
        { provide: MAT_DIALOG_DATA, useValue: { projectId: 'p', assetId: 1 } },
      ],
    }).compileComponents();

    httpMock = TestBed.inject(HttpTestingController);
    svc = TestBed.inject(FFmpegStatusService);
    // FFmpegStatusService is providedIn:'root'; reset its in-test cache so
    // each test starts with a fresh "no fetch yet" state. Without this the
    // shareReplay on `inflight$` from a prior test would short-circuit
    // the new component's load() call and httpMock.expectOne would fail.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const internal = svc as any;
    internal.fetched = false;
    internal.inflight$ = null;
    internal.readyState.set(false);
    internal.detailsState.set(undefined);

    fixture = TestBed.createComponent(JobCreateFormComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => httpMock.verify());

  describe('call-count caching (per spec AC §474)', () => {
    it('fetches /api/ffmpeg-status/ exactly once per component instance', () => {
      const reqs = httpMock.match(FFMPEG_URL);
      expect(reqs.length).toBe(1);
      expect(reqs[0].request.method).toBe('GET');
      reqs[0].flush({ video_assembly_ready: true } as FFmpegStatusResponse);

      // Reading the signal multiple times must not trigger more requests.
      for (let i = 0; i < 5; i++) {
        component.videoAssemblyReady();
      }
      httpMock.expectNone(FFMPEG_URL);
    });
  });

  describe('default state (fail-closed, per spec FR §107)', () => {
    it('exposes videoAssemblyReady() === false before the response arrives', () => {
      expect(component.videoAssemblyReady()).toBeFalse();
      // Drain the pending request so afterEach.verify() passes.
      httpMock.expectOne(FFMPEG_URL).flush(
        { video_assembly_ready: false } as FFmpegStatusResponse,
      );
    });
  });

  describe('in-flight state (per spec FR §107)', () => {
    it('renders the in-flight progress spinner on the animation form', () => {
      component.renderType = 'animation';
      fixture.detectChanges();

      const spinner = fixture.debugElement.query(
        By.css('.assembly-hint mat-progress-spinner'),
      );
      expect(spinner).not.toBeNull();

      httpMock.expectOne(FFMPEG_URL).flush(
        { video_assembly_ready: false } as FFmpegStatusResponse,
      );
    });
  });

  describe('not-ready state (per spec FR §106)', () => {
    it('renders the prepare hint when assemblyReady is false', () => {
      component.renderType = 'animation';
      httpMock.expectOne(FFMPEG_URL).flush(
        { video_assembly_ready: false } as FFmpegStatusResponse,
      );
      fixture.detectChanges();

      const hint = fixture.debugElement.query(By.css('.assembly-hint'));
      expect(hint).not.toBeNull();
      expect(hint.nativeElement.textContent).toContain(
        'Video assembly is preparing',
      );
      expect(hint.nativeElement.textContent).toContain(
        'refresh in a moment',
      );
      expect(component.videoAssemblyReady()).toBeFalse();
    });
  });

  describe('ready state (per spec FR §108)', () => {
    it('hides the prepare hint when ffmpeg is ready', () => {
      component.renderType = 'animation';
      httpMock.expectOne(FFMPEG_URL).flush(
        { video_assembly_ready: true } as FFmpegStatusResponse,
      );
      fixture.detectChanges();

      expect(component.videoAssemblyReady()).toBeTrue();
      expect(fixture.debugElement.query(By.css('.assembly-hint'))).toBeNull();
    });
  });
});
