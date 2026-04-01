// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, tap, map } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface UserInfo {
  username: string;
  is_staff: boolean;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly authUrl = `${environment.apiBaseUrl}/auth`;

  readonly isAuthenticated$ = new BehaviorSubject<boolean>(false);

  private currentUser: UserInfo | null = null;

  get user(): UserInfo | null {
    return this.currentUser;
  }

  fetchCsrfToken(): Observable<unknown> {
    return this.http.get(`${this.authUrl}/csrf/`);
  }

  login(username: string, password: string): Observable<UserInfo> {
    return this.http
      .post<UserInfo>(`${this.authUrl}/login/`, { username, password })
      .pipe(
        tap((user) => {
          this.currentUser = user;
          this.isAuthenticated$.next(true);
        })
      );
  }

  logout(): Observable<void> {
    return this.http.post<void>(`${this.authUrl}/logout/`, {}).pipe(
      tap(() => {
        this.currentUser = null;
        this.isAuthenticated$.next(false);
      })
    );
  }

  getCurrentUser(): Observable<UserInfo> {
    return this.http.get<UserInfo>(`${this.authUrl}/user/`).pipe(
      tap((user) => {
        this.currentUser = user;
        this.isAuthenticated$.next(true);
      })
    );
  }

  setUnauthenticated(): void {
    this.currentUser = null;
    this.isAuthenticated$.next(false);
  }
}
