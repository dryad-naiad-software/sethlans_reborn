// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { AnimationFrame } from '../../core/services/animation.service';
import { Job } from '../../core/services/job.service';

/** Common interface for frames displayed in the filmstrip and result dialog. */
export interface FilmstripFrame {
  id: number;
  frameNumber: number;
  thumbnail: string | null;
  outputFile: string | null;
}

/** Map an AnimationFrame (tiled animation) to a FilmstripFrame. */
export function fromAnimationFrame(frame: AnimationFrame): FilmstripFrame {
  return {
    id: frame.id,
    frameNumber: frame.frame_number,
    thumbnail: frame.thumbnail,
    outputFile: frame.output_file,
  };
}

/** Map a Job (standard animation) to a FilmstripFrame. */
export function fromJob(job: Job): FilmstripFrame {
  return {
    id: job.id,
    frameNumber: job.start_frame,
    thumbnail: job.thumbnail,
    outputFile: job.output_file,
  };
}
