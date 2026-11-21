// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed, discardPeriodicTasks, fakeAsync, tick } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { SequenceService, VERSIONS_POLL_MS, sequenceErrorText } from './sequence.service';
import { SequenceSubmission } from './sequence.types';
import { discovery, version } from '../../features/projects/sequence.fixtures';

const BASE = '/api/sequence-submissions';

function page<T>(results: T[]) {
  return { count: results.length, next: null, previous: null, results };
}

describe('SequenceService', () => {
  let service: SequenceService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(SequenceService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists a project\'s versions from the paginated answer', () => {
    let rows: SequenceSubmission[] = [];
    service.list('p1').subscribe(r => { rows = r; });
    const req = http.expectOne(r => r.url === `${BASE}/` && r.params.get('project') === 'p1');
    expect(req.request.method).toBe('GET');
    req.flush(page([version()]));
    expect(rows.length).toBe(1);
    expect(rows[0].name).toBe('Ep1 v2');
  });

  it('lists discoveries, starts one and reads one', () => {
    service.listDiscoveries('p1').subscribe(r => expect(r[0].id).toBe(3));
    http.expectOne(r => r.url === '/api/sequence-discoveries/' && r.params.get('project') === 'p1')
      .flush(page([discovery()]));
    const key = { asset: 5, map: 'm', sequence: 's', preset: 'p' };
    service.discover(key).subscribe();
    const post = http.expectOne('/api/sequence-discoveries/');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual(key);
    post.flush(discovery());
    service.getDiscovery(3).subscribe();
    http.expectOne('/api/sequence-discoveries/3/').flush(discovery());
  });

  it('posts the actions to their endpoints', () => {
    const calls: [() => void, string, string][] = [
      [() => service.cancel(7).subscribe(), 'POST', `${BASE}/7/cancel/`],
      [() => service.rerenderShot(7, 2).subscribe(), 'POST', `${BASE}/7/shots/2/rerender/`],
      [() => service.retrySound(7).subscribe(), 'POST', `${BASE}/7/retry-sound/`],
      [() => service.retryVideo(7).subscribe(), 'POST', `${BASE}/7/retry-video/`],
      [() => service.mergeSound(7).subscribe(), 'POST', `${BASE}/7/merge-sound/`],
      [() => service.delete(7).subscribe(), 'DELETE', `${BASE}/7/`],
      [() => service.get(7).subscribe(), 'GET', `${BASE}/7/`],
    ];
    for (const [call, method, url] of calls) {
      call();
      const req = http.expectOne(url);
      expect(req.request.method).toBe(method);
      req.flush({});
    }
  });

  it('creates a version with the given body', () => {
    const body = {
      asset: 5, map: 'm', sequence: 's', preset: 'p', frame_format: 'PNG' as const,
      video_settings: null, sound: false, merge_sound: false, draft: null,
    };
    service.create(body).subscribe();
    const req = http.expectOne(`${BASE}/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush(version());
  });

  it('builds plain download links for a version and for a shot', () => {
    expect(service.downloadUrl(7, 'video')).toBe(`${BASE}/7/download-video/`);
    expect(service.downloadUrl(7, 'video_sound')).toBe(`${BASE}/7/download-video/?sound=1`);
    expect(service.downloadUrl(7, 'sound')).toBe(`${BASE}/7/download-sound/`);
    expect(service.downloadUrl(7, 'frames')).toBe(`${BASE}/7/download-frames/`);
    expect(service.downloadUrl(7, 'video', 3)).toBe(`${BASE}/7/shots/3/download-video/`);
    expect(service.downloadUrl(7, 'frames', 3)).toBe(`${BASE}/7/shots/3/download-frames/`);
  });

  it('polls the versions every 10 s and stops on 403', fakeAsync(() => {
    let errored = false;
    const sub = service.pollList('p1').subscribe({ error: () => { errored = true; } });
    http.expectOne(r => r.url === `${BASE}/`).flush(page([]));
    tick(VERSIONS_POLL_MS);
    http.expectOne(r => r.url === `${BASE}/`).flush({ detail: 'no' }, { status: 403, statusText: 'Forbidden' });
    expect(errored).toBeTrue();
    tick(VERSIONS_POLL_MS * 2);
    http.expectNone(r => r.url === `${BASE}/`);
    sub.unsubscribe();
    discardPeriodicTasks();
  }));

  it('polls on after a 500 and stops on 401', fakeAsync(() => {
    let errored = false;
    const sub = service.pollList('p1').subscribe({ error: () => { errored = true; } });
    http.expectOne(r => r.url === `${BASE}/`).flush({}, { status: 500, statusText: 'Server Error' });
    expect(errored).toBeFalse();
    tick(VERSIONS_POLL_MS);
    http.expectOne(r => r.url === `${BASE}/`).flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(errored).toBeTrue();
    tick(VERSIONS_POLL_MS);
    http.expectNone(r => r.url === `${BASE}/`);
    sub.unsubscribe();
    discardPeriodicTasks();
  }));
});

describe('sequenceErrorText', () => {
  it('reads error, detail and field errors, else the fallback', () => {
    expect(sequenceErrorText({ error: { error: 'Running.' } }, 'x')).toBe('Running.');
    expect(sequenceErrorText({ error: { detail: 'Unknown keys: a.' } }, 'x')).toBe('Unknown keys: a.');
    expect(sequenceErrorText({ error: { shots: ['Read the shots first.'] } }, 'x')).toBe('shots: Read the shots first.');
    expect(sequenceErrorText({ error: '<html>500</html>' }, 'x')).toBe('x');
    expect(sequenceErrorText(null, 'x')).toBe('x');
  });
});
