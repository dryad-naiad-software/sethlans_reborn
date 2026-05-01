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

describe('authInterceptor — generic auth error handling', () => {
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

  describe('401 handling', () => {
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
  });

  describe('403 handling', () => {
    it('shows snackbar for 403 responses', () => {
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

  describe('pass-through for unexpected codes', () => {
    it('does not navigate on 418', () => {
      configure('/dashboard');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush({}, { status: 418, statusText: 'Teapot' });
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });

    it('does not navigate on 500', () => {
      configure('/dashboard');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush({}, { status: 500, statusText: 'ISE' });
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });

    it('does not auto-redirect on 503', () => {
      configure('/dashboard');
      http.get('/api/projects/').subscribe({ error: () => {} });
      const req = httpMock.expectOne('/api/projects/');
      req.flush(
        { detail: 'Service unavailable' },
        { status: 503, statusText: 'Service Unavailable' },
      );
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });
  });
});

describe('authInterceptor — CSRF cookie attachment', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  let cookieSpy: jasmine.Spy;
  let originalCookieDescriptor: PropertyDescriptor | undefined;

  function configure(cookieValue: string) {
    originalCookieDescriptor = Object.getOwnPropertyDescriptor(
      Document.prototype, 'cookie',
    );
    cookieSpy = jasmine.createSpy('cookieGet').and.returnValue(cookieValue);
    Object.defineProperty(document, 'cookie', {
      configurable: true,
      get: cookieSpy,
    });

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: Router, useValue: jasmine.createSpyObj('Router', ['navigate']) },
        { provide: MatSnackBar, useValue: jasmine.createSpyObj('MatSnackBar', ['open']) },
        { provide: AuthService, useValue: jasmine.createSpyObj('AuthService', ['setUnauthenticated']) },
      ],
    });
    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
  }

  afterEach(() => {
    httpMock.verify();
    if (originalCookieDescriptor) {
      Object.defineProperty(document, 'cookie', originalCookieDescriptor);
    }
  });

  it('attaches X-CSRFToken header for POST when csrftoken cookie present', () => {
    configure('csrftoken=abc123');
    http.post('/api/projects/', {}).subscribe();
    const req = httpMock.expectOne('/api/projects/');
    expect(req.request.headers.get('X-CSRFToken')).toBe('abc123');
    req.flush({});
  });

  it('does NOT attach X-CSRFToken header for GET', () => {
    configure('csrftoken=abc123');
    http.get('/api/projects/').subscribe();
    const req = httpMock.expectOne('/api/projects/');
    expect(req.request.headers.get('X-CSRFToken')).toBeNull();
    req.flush({});
  });

  it('omits X-CSRFToken for POST when cookie is absent', () => {
    configure('');
    http.post('/api/projects/', {}).subscribe();
    const req = httpMock.expectOne('/api/projects/');
    expect(req.request.headers.get('X-CSRFToken')).toBeNull();
    req.flush({});
  });
});

describe('authInterceptor FR-9 grep gate (no HTTP required)', () => {
  it('interceptor source does not reference /setup/bootstrap-error', () => {
    // The old error flow routed to /setup/bootstrap-error; FR-9 deleted
    // that route. The interceptor must no longer mention the path.
    const source = authInterceptor.toString();
    expect(source).not.toMatch(/bootstrap-error/);
  });

  it('interceptor source does not reference setup envelope codes', () => {
    // FR-DEL8b removed the setup-envelope branches entirely.
    const source = authInterceptor.toString();
    expect(source).not.toMatch(/setup_in_progress|setup_complete|setup_session_conflict/);
  });
});
