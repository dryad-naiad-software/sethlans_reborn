// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { HttpErrorResponse } from '@angular/common/http';
import { of, throwError, Subject } from 'rxjs';
import { DoneComponent, _doneNavigation } from './done.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';
import { RestartPollService, PollOutcome } from '../services/restart-poll.service';
import { SetupSummary } from '../models/setup.models';

const MOCK_SUMMARY: SetupSummary = {
  manager_url: 'https://localhost:8080',
  admin_username: 'admin',
  enrollment_key: 'ABC123DEF',
  cert_fingerprint: 'AA:BB:CC:DD',
  topology: 'manager',
};

describe('DoneComponent', () => {
  let component: DoneComponent;
  let fixture: ComponentFixture<DoneComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let mockPoll: jasmine.SpyObj<RestartPollService>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', [
      'getSummary', 'getHealth', 'requestRestart',
    ]);
    mockPoll = jasmine.createSpyObj('RestartPollService', ['poll']);

    await TestBed.configureTestingModule({
      imports: [DoneComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
        { provide: RestartPollService, useValue: mockPoll },
      ],
    }).compileComponents();

    state = TestBed.inject(SetupStateService);
  });

  function createAndDetect() {
    fixture = TestBed.createComponent(DoneComponent);
    component = fixture.componentInstance;
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
  }

  describe('successful summary load', () => {
    beforeEach(() => {
      state.setAdminPassword('secret');
      mockApi.getSummary.and.returnValue(of(MOCK_SUMMARY));
      createAndDetect();
    });

    it('creates', () => expect(component).toBeTruthy());

    it('clears sensitive data on init', () => {
      expect(state.getAdminPassword()).toBeNull();
    });

    it('loads summary', () => {
      expect(component.summary()).toEqual(MOCK_SUMMARY);
      expect(component.loading()).toBeFalse();
    });

    it('renders summary values', () => {
      const el = fixture.nativeElement as HTMLElement;
      expect(el.textContent).toContain('https://localhost:8080');
      expect(el.textContent).toContain('admin');
      expect(el.textContent).toContain('ABC123DEF');
      expect(el.textContent).toContain('AA:BB:CC:DD');
    });

    it('renders a "Finish setup" button', () => {
      const el = fixture.nativeElement as HTMLElement;
      const text = el.textContent ?? '';
      expect(text).toContain('Finish setup');
    });
  });

  describe('load error', () => {
    beforeEach(() => {
      mockApi.getSummary.and.returnValue(throwError(() => new Error('fail')));
      createAndDetect();
    });

    it('sets loading to false on error', () => {
      expect(component.loading()).toBeFalse();
    });

    it('opens snackbar on error', () => {
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to load summary', 'Dismiss', { duration: 5000 });
    });

    it('leaves summary null', () => {
      expect(component.summary()).toBeNull();
    });
  });

  describe('copyToClipboard', () => {
    beforeEach(() => {
      mockApi.getSummary.and.returnValue(of(MOCK_SUMMARY));
      createAndDetect();
    });

    it('shows success snackbar on copy', fakeAsync(() => {
      spyOn(navigator.clipboard, 'writeText')
        .and.returnValue(Promise.resolve());
      component.copyToClipboard('test');
      tick();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Copied to clipboard', 'Dismiss', { duration: 2000 });
    }));

    it('shows failure snackbar on copy failure', fakeAsync(() => {
      spyOn(navigator.clipboard, 'writeText')
        .and.returnValue(Promise.reject('denied'));
      component.copyToClipboard('test');
      tick();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to copy', 'Dismiss', { duration: 3000 });
    }));
  });

  describe('finishSetup flow', () => {
    let pollSubject: Subject<PollOutcome>;
    let goToSpy: jasmine.Spy;
    const originalGoTo = _doneNavigation.goTo;

    beforeEach(() => {
      mockApi.getSummary.and.returnValue(of(MOCK_SUMMARY));
      pollSubject = new Subject<PollOutcome>();
      mockPoll.poll.and.returnValue(pollSubject.asObservable());
      goToSpy = jasmine.createSpy('goTo');
      _doneNavigation.goTo = goToSpy;
    });

    afterEach(() => {
      _doneNavigation.goTo = originalGoTo;
    });

    it('captures boot_id via getHealth then POSTs restart and starts poll',
      fakeAsync(() => {
        mockApi.getHealth.and.returnValue(
          of({ boot_id: 'boot-before', setup_mode: true }),
        );
        mockApi.requestRestart.and.returnValue(of(void 0));
        createAndDetect();

        component.finishSetup();
        tick();

        expect(mockApi.getHealth).toHaveBeenCalled();
        expect(mockApi.requestRestart).toHaveBeenCalled();
        expect(mockPoll.poll).toHaveBeenCalledWith('boot-before');
        expect(component.phase()).toBe('restarting');
      }),
    );

    it('tolerates 409 from requestRestart (restart already in flight)',
      fakeAsync(() => {
        mockApi.getHealth.and.returnValue(
          of({ boot_id: 'bid', setup_mode: true }),
        );
        mockApi.requestRestart.and.returnValue(
          throwError(() => new HttpErrorResponse({
            status: 409, statusText: 'Conflict',
          })),
        );
        createAndDetect();

        component.finishSetup();
        tick();

        expect(mockPoll.poll).toHaveBeenCalledWith('bid');
        expect(component.phase()).toBe('restarting');
      }),
    );

    it('redirects to /login via window.location.href on boot_changed',
      fakeAsync(() => {
        mockApi.getHealth.and.returnValue(
          of({ boot_id: 'old', setup_mode: true }),
        );
        mockApi.requestRestart.and.returnValue(of(void 0));
        createAndDetect();

        component.finishSetup();
        tick();
        pollSubject.next('boot_changed');
        tick();

        expect(goToSpy).toHaveBeenCalledWith('/login');
      }),
    );

    it('sets phase to error on timed_out outcome', fakeAsync(() => {
      mockApi.getHealth.and.returnValue(
        of({ boot_id: 'old', setup_mode: true }),
      );
      mockApi.requestRestart.and.returnValue(of(void 0));
      createAndDetect();

      component.finishSetup();
      tick();
      pollSubject.next('timed_out');
      tick();

      expect(component.phase()).toBe('error');
    }));

    it('proceeds with empty bootIdBefore when getHealth fails',
      fakeAsync(() => {
        mockApi.getHealth.and.returnValue(
          throwError(() => new HttpErrorResponse({ status: 0 })),
        );
        mockApi.requestRestart.and.returnValue(of(void 0));
        createAndDetect();

        component.finishSetup();
        tick();
        expect(mockPoll.poll).toHaveBeenCalledWith('');
      }),
    );

    it('retryPoll restarts polling after an error outcome', fakeAsync(() => {
      mockApi.getHealth.and.returnValue(
        of({ boot_id: 'old', setup_mode: true }),
      );
      mockApi.requestRestart.and.returnValue(of(void 0));
      createAndDetect();

      component.finishSetup();
      tick();
      pollSubject.next('timed_out');
      tick();
      expect(component.phase()).toBe('error');

      // New poll observable for the retry.
      const retrySubject = new Subject<PollOutcome>();
      mockPoll.poll.and.returnValue(retrySubject.asObservable());

      component.retryPoll();
      tick();
      expect(component.phase()).toBe('restarting');
      expect(mockPoll.poll).toHaveBeenCalledTimes(2);
    }));
  });
});
