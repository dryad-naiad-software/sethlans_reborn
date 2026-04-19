// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import {
  HttpClient,
  provideHttpClient,
  withInterceptors,
} from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { Router } from '@angular/router';
import { MatSnackBar } from '@angular/material/snack-bar';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from '../services/auth.service';

function envelope(
  code: string,
  message = 'error',
): { error: { code: string; message: string; details: Record<string, unknown> } } {
  return { error: { code, message, details: {} } };
}

describe('authInterceptor — setup envelope routing', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  let routerSpy: jasmine.SpyObj<Router> & { url: string };
  let snackBarSpy: jasmine.SpyObj<MatSnackBar>;
  let authSpy: jasmine.SpyObj<AuthService>;

  function configure(currentUrl: string) {
    routerSpy = jasmine.createSpyObj<Router>(
      'Router', ['navigate'],
    ) as jasmine.SpyObj<Router> & { url: string };
    (routerSpy as { url: string }).url = currentUrl;
    snackBarSpy = jasmine.createSpyObj('MatSnackBar', ['open']);
    authSpy = jasmine.createSpyObj('AuthService', ['setUnauthenticated']);

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: Router, useValue: routerSpy },
        { provide: MatSnackBar, useValue: snackBarSpy },
        { provide: AuthService, useValue: authSpy },
      ],
    });
    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
  }

  afterEach(() => httpMock.verify());

  describe('setup_in_progress (403)', () => {
    it('navigates to /setup with preserve when current URL is /dashboard', () => {
      configure('/dashboard');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush(envelope('setup_in_progress'), {
        status: 403, statusText: 'Forbidden',
      });
      expect(routerSpy.navigate).toHaveBeenCalledOnceWith(
        ['/setup'], { queryParamsHandling: 'preserve' },
      );
    });

    it('does NOT navigate when already on /setup', () => {
      configure('/setup');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush(envelope('setup_in_progress'), {
        status: 403, statusText: 'Forbidden',
      });
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });

    it('does NOT navigate on /setup with query params', () => {
      configure('/setup?token=abc');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush(envelope('setup_in_progress'), {
        status: 403, statusText: 'Forbidden',
      });
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });
  });

  describe('setup_complete', () => {
    it('navigates to /login on 404 with setup_complete code', () => {
      configure('/dashboard');
      http.get('/api/setup/status/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/setup/status/');
      req.flush(envelope('setup_complete'), {
        status: 404, statusText: 'Not Found',
      });
      expect(routerSpy.navigate).toHaveBeenCalledOnceWith(['/login']);
    });
  });

  describe('setup_session_conflict', () => {
    it('navigates to /login on 409 setup_session_conflict', () => {
      configure('/setup');
      http.post('/api/setup/topology/', {}).subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/setup/topology/');
      req.flush(envelope('setup_session_conflict'), {
        status: 409, statusText: 'Conflict',
      });
      expect(routerSpy.navigate).toHaveBeenCalledOnceWith(['/login']);
    });
  });

  describe('invalid_token', () => {
    it('does NOT navigate (handled in APP_INITIALIZER)', () => {
      configure('/dashboard');
      http.get('/api/setup/bootstrap/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/setup/bootstrap/');
      req.flush(envelope('invalid_token'), {
        status: 403, statusText: 'Forbidden',
      });
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });
  });

  describe('non-envelope errors', () => {
    it('redirects 401 on non-login URL to /login', () => {
      configure('/dashboard');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush({ detail: 'auth required' }, {
        status: 401, statusText: 'Unauthorized',
      });
      expect(authSpy.setUnauthenticated).toHaveBeenCalled();
      expect(routerSpy.navigate).toHaveBeenCalledOnceWith(['/login']);
    });

    it('does NOT redirect 401 on login URL', () => {
      configure('/login');
      http.post('/api/auth/login/', {}).subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/auth/login/');
      req.flush({ detail: 'bad creds' }, {
        status: 401, statusText: 'Unauthorized',
      });
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });

    it('shows snackbar for 403 without envelope code', () => {
      configure('/dashboard');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush({ detail: 'nope' }, {
        status: 403, statusText: 'Forbidden',
      });
      expect(snackBarSpy.open).toHaveBeenCalled();
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });
  });

  describe('503 behavior (legacy removed)', () => {
    it('does NOT auto-redirect on 503 without envelope code', () => {
      configure('/dashboard');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush(
        { detail: 'Setup not complete.' },
        { status: 503, statusText: 'Service Unavailable' },
      );
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });
  });
});
