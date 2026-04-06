// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { fromAnimationFrame, fromJob, FilmstripFrame } from './filmstrip-frame';
import { AnimationFrame } from '../../core/services/animation.service';
import { Job } from '../../core/services/job.service';

describe('filmstrip-frame utilities', () => {
  describe('fromAnimationFrame', () => {
    it('should map an AnimationFrame to a FilmstripFrame', () => {
      const frame: AnimationFrame = {
        id: 42,
        frame_number: 7,
        status: 'DONE',
        output_file: '/media/output/frame_007.png',
        thumbnail: '/media/thumbs/frame_007.png',
        render_time_seconds: 12.5,
      };

      const result: FilmstripFrame = fromAnimationFrame(frame);

      expect(result).toEqual({
        id: 42,
        frameNumber: 7,
        thumbnail: '/media/thumbs/frame_007.png',
        outputFile: '/media/output/frame_007.png',
      });
    });

    it('should handle null thumbnail and output_file', () => {
      const frame: AnimationFrame = {
        id: 1, frame_number: 1, status: 'QUEUED',
        output_file: null, thumbnail: null, render_time_seconds: null,
      };

      const result = fromAnimationFrame(frame);

      expect(result.thumbnail).toBeNull();
      expect(result.outputFile).toBeNull();
    });
  });

  describe('fromJob', () => {
    it('should map a Job to a FilmstripFrame using start_frame', () => {
      const job = {
        id: 99, name: 'Frame 5', start_frame: 5, end_frame: 5,
        status: 'DONE', output_file: '/media/output/frame_005.png',
        thumbnail: '/media/thumbs/frame_005.png',
      } as Job;

      const result: FilmstripFrame = fromJob(job);

      expect(result).toEqual({
        id: 99,
        frameNumber: 5,
        thumbnail: '/media/thumbs/frame_005.png',
        outputFile: '/media/output/frame_005.png',
      });
    });

    it('should handle null thumbnail and output_file', () => {
      const job = {
        id: 1, start_frame: 1, thumbnail: null, output_file: null,
      } as Job;

      const result = fromJob(job);

      expect(result.thumbnail).toBeNull();
      expect(result.outputFile).toBeNull();
    });
  });
});
