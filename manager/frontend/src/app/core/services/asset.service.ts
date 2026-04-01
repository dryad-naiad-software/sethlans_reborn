import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface Asset {
  id: number;
  project: number;
  file: string;
  filename: string;
  uploaded_at: string;
}

@Injectable({ providedIn: 'root' })
export class AssetService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/assets`;

  list(): Observable<Asset[]> {
    return this.http.get<Asset[]>(`${this.baseUrl}/`);
  }

  upload(projectId: number, file: File): Observable<Asset> {
    const formData = new FormData();
    formData.append('project', projectId.toString());
    formData.append('file', file);
    return this.http.post<Asset>(`${this.baseUrl}/`, formData);
  }
}
