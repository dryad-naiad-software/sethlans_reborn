// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Router } from '@angular/router';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { DoneComponent } from './done.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';
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
  let mockRouter: jasmine.SpyObj<Router>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', ['getSummary']);
    mockRouter = jasmine.createSpyObj('Router', ['navigate']);

    await TestBed.configureTestingModule({
      imports: [DoneComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
        { provide: Router, useValue: mockRouter },
      ],
    }).compileComponents();

    state = TestBed.inject(SetupStateService);
  });

  /** Creates fixture, spies on snackBar, then runs detectChanges. */
  function createAndDetect() {
    fixture = TestBed.createComponent(DoneComponent);
    component = fixture.componentInstance;
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
  }

  describe('successful load', () => {
    beforeEach(() => {
      state.setAdminPassword('secret');
      mockApi.getSummary.and.returnValue(of(MOCK_SUMMARY));
      createAndDetect();
    });

    it('should create', () => {
      expect(component).toBeTruthy();
    });

    it('should clear sensitive data on init', () => {
      expect(state.getAdminPassword()).toBeNull();
    });

    it('should load summary from api', () => {
      expect(component.summary).toEqual(MOCK_SUMMARY);
    });

    it('should set loading to false after load', () => {
      expect(component.loading).toBeFalse();
    });

    it('should display summary data', () => {
      const el = fixture.nativeElement as HTMLElement;
      expect(el.textContent).toContain('https://localhost:8080');
      expect(el.textContent).toContain('admin');
      expect(el.textContent).toContain('ABC123DEF');
      expect(el.textContent).toContain('AA:BB:CC:DD');
    });
  });

  describe('load error', () => {
    beforeEach(() => {
      mockApi.getSummary.and.returnValue(throwError(() => new Error('fail')));
      createAndDetect();
    });

    it('should set loading to false on error', () => {
      expect(component.loading).toBeFalse();
    });

    it('should show snackbar on error', () => {
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to load summary', 'Dismiss', { duration: 5000 });
    });

    it('should leave summary as null', () => {
      expect(component.summary).toBeNull();
    });
  });

  describe('openSethlans', () => {
    beforeEach(() => {
      mockApi.getSummary.and.returnValue(of(MOCK_SUMMARY));
      createAndDetect();
    });

    it('should navigate to root', () => {
      component.openSethlans();
      expect(mockRouter.navigate).toHaveBeenCalledWith(['/']);
    });
  });

  describe('copyToClipboard', () => {
    beforeEach(() => {
      mockApi.getSummary.and.returnValue(of(MOCK_SUMMARY));
      createAndDetect();
    });

    it('should show success snackbar on copy', fakeAsync(() => {
      spyOn(navigator.clipboard, 'writeText')
        .and.returnValue(Promise.resolve());
      component.copyToClipboard('test');
      tick();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Copied to clipboard', 'Dismiss', { duration: 2000 });
    }));

    it('should show failure snackbar when copy fails', fakeAsync(() => {
      spyOn(navigator.clipboard, 'writeText')
        .and.returnValue(Promise.reject('denied'));
      component.copyToClipboard('test');
      tick();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to copy', 'Dismiss', { duration: 3000 });
    }));
  });
});
