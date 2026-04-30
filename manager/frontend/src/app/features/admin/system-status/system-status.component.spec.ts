// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { By } from '@angular/platform-browser';
import { SystemStatusComponent } from './system-status.component';
import { FFmpegStatusResponse } from '../../../core/services/ffmpeg-status.service';

const URL = '/api/ffmpeg-status/';

const ADMIN_PAYLOAD: FFmpegStatusResponse = {
  video_assembly_ready: true,
  ffmpeg: {
    source: 'system',
    version: '8.1',
    path: '/usr/local/bin/ffmpeg',
    status: 'ready',
    error: null,
  },
};

describe('SystemStatusComponent', () => {
  let fixture: ComponentFixture<SystemStatusComponent>;
  let component: SystemStatusComponent;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SystemStatusComponent, NoopAnimationsModule],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(SystemStatusComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should create', () => {
    fixture.detectChanges();
    httpMock.expectOne(URL).flush(ADMIN_PAYLOAD);
    expect(component).toBeTruthy();
  });

  it('issues GET /api/ffmpeg-status/ exactly once on init', () => {
    fixture.detectChanges();
    const req = httpMock.expectOne(URL);
    expect(req.request.method).toBe('GET');
    req.flush(ADMIN_PAYLOAD);
    httpMock.expectNone(URL);
  });

  it('does NOT use a polling timer (no extra requests after 5 simulated seconds)',
    fakeAsync(() => {
      fixture.detectChanges();
      const req = httpMock.expectOne(URL);
      req.flush(ADMIN_PAYLOAD);

      // Simulate component lifetime ticking forward.
      tick(5000);
      fixture.detectChanges();

      // No additional requests should have been issued.
      const followups = httpMock.match(URL);
      expect(followups.length).toBe(0);
    }),
  );

  it('renders one <mat-card> per part (FFmpeg only today)', () => {
    fixture.detectChanges();
    httpMock.expectOne(URL).flush(ADMIN_PAYLOAD);
    fixture.detectChanges();
    const cards = fixture.debugElement.queryAll(By.css('mat-card.part-card'));
    expect(cards.length).toBe(1);
  });

  it('does NOT use <mat-table> (per spec FR §101)', () => {
    fixture.detectChanges();
    httpMock.expectOne(URL).flush(ADMIN_PAYLOAD);
    fixture.detectChanges();
    expect(fixture.debugElement.query(By.css('mat-table'))).toBeNull();
    expect(fixture.debugElement.query(By.css('mat-card'))).not.toBeNull();
  });

  describe('admin payload — ready FFmpeg', () => {
    beforeEach(() => {
      fixture.detectChanges();
      httpMock.expectOne(URL).flush(ADMIN_PAYLOAD);
      fixture.detectChanges();
    });

    it('renders the part name', () => {
      const title = fixture.nativeElement.querySelector('mat-card-title');
      expect(title?.textContent).toContain('FFmpeg');
    });

    it('renders the source field', () => {
      const text = fixture.nativeElement.querySelector('.part-card')
        .textContent as string;
      expect(text).toContain('system');
    });

    it('renders the version field', () => {
      const text = fixture.nativeElement.querySelector('.part-card')
        .textContent as string;
      expect(text).toContain('8.1');
    });

    it('renders the path field', () => {
      const pathEl = fixture.nativeElement.querySelector('.path-value');
      expect(pathEl?.textContent).toContain('/usr/local/bin/ffmpeg');
    });

    it('renders the status field', () => {
      const text = fixture.nativeElement.querySelector('.part-card')
        .textContent as string;
      expect(text).toContain('ready');
    });

    it('does not render an error string when error is null', () => {
      const errEl = fixture.nativeElement.querySelector('.error-text');
      expect(errEl).toBeNull();
    });
  });

  describe('failed status with error string', () => {
    it('renders the error string in the card', () => {
      fixture.detectChanges();
      httpMock.expectOne(URL).flush({
        video_assembly_ready: false,
        ffmpeg: {
          source: 'bundled',
          version: '8.1',
          path: '/data/bin/ffmpeg/8.1/ffmpeg',
          status: 'failed',
          error: 'checksum_mismatch',
        },
      } as FFmpegStatusResponse);
      fixture.detectChanges();

      const errEl = fixture.nativeElement.querySelector('.error-text');
      expect(errEl).not.toBeNull();
      expect(errEl.textContent).toContain('checksum_mismatch');

      const text = fixture.nativeElement.querySelector('.part-card')
        .textContent as string;
      expect(text).toContain('failed');
    });
  });

  describe('installing status', () => {
    it('renders the installing status string in the card', () => {
      fixture.detectChanges();
      httpMock.expectOne(URL).flush({
        video_assembly_ready: false,
        ffmpeg: {
          source: 'bundled',
          version: '8.1',
          path: '',
          status: 'installing',
          error: null,
        },
      } as FFmpegStatusResponse);
      fixture.detectChanges();

      const text = fixture.nativeElement.querySelector('.part-card')
        .textContent as string;
      expect(text).toContain('installing');
    });
  });

  describe('error handling', () => {
    it('shows error message on HTTP failure', () => {
      fixture.detectChanges();
      const req = httpMock.expectOne(URL);
      req.flush(
        { detail: 'oops' },
        { status: 500, statusText: 'ISE' },
      );
      fixture.detectChanges();

      expect(component.errorMessage()).toBe(
        'Unable to load system status. Refresh to retry.',
      );
      // The component also opens a MatSnackBar on failure, but the snack-bar
      // mock provided to TestBed is not the same instance the component
      // injects via standalone-imports of MatSnackBarModule. The visible
      // error-card path is what the user sees and what we assert here.
      const errCard = fixture.nativeElement.querySelector('.error-card');
      expect(errCard).not.toBeNull();
      expect(errCard.textContent).toContain(
        'Unable to load system status',
      );
    });

    it('clears the loading flag on HTTP failure', () => {
      fixture.detectChanges();
      const req = httpMock.expectOne(URL);
      req.flush(
        { detail: 'oops' },
        { status: 500, statusText: 'ISE' },
      );
      fixture.detectChanges();
      expect(component.loading()).toBeFalse();
    });
  });
});
