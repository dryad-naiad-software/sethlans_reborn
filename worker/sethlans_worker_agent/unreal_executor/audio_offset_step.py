# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The offset step of an audio job (sequence rendering D23, D24, FR-SA-9,
FR-SA-10), run after the render passed FR-SA-5.

``measure(flow, wav_path, rate_fraction)``:

1. the export commandlet: ``command.build_prep`` with
   ``sethlans_audio_export.py`` (no GPU, no adapter), its params in
   ``export_params.json``, its result in ``audio_sources.json``, the raw
   sources in the job's ``audio_src\\`` (created owner-only), the phase
   ``prep`` attempt 2 (``logs\\prep.2.log``, ``PREP_TIMEOUT_SECONDS``),
   through ``PhaseRunner.launch`` like every launch;
2. the strict parse (``sequence_prep.parse_sources``); every ``.wav`` (the
   sources and the render) is opened with ``fs_safe.open_output_file`` and
   read by ``sound_mix.Source``; a source's header rate must equal its
   manifest rate;
3. ``sound_offset.measure`` over the run's shot parts.

It **never fails the job**: a failed or invalid export, an unreadable
source or a refused launch gives an ``unavailable`` object whose reason
names the step or the field (never a path, never an exception's message;
a ``JobFailure`` text is the worker's own). A cancel, a shutdown and
survivors propagate. ``audio_src\\`` is removed afterwards, whatever
happened.

The export's ``warnings`` (#284) join the job's summary warnings whenever
its result file was read, whether the offset was measured, fell back, or
the script itself failed (what it found before failing still holds). A
launch that timed out, crashed or was refused leaves the result unread,
so nothing is passed on. The field is untrusted: at most
``shots_result.MAX_WARNINGS`` entries are read, each cleaned of control
characters and cut to ``WARNING_MAX_CHARS`` like every other prep warning;
entries that are not text are skipped, and a field that is not a list (or
holds anything but text) adds the fixed ``INVALID_WARNINGS`` warning
instead. A warning the summary already has is not added twice.
"""
import contextlib
import logging
import wave
from pathlib import Path
from typing import Tuple

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import job_dir, queue_prep, render_check, sequence_prep, sound_mix
from sethlans_worker_agent.unreal_executor import sound_offset
from sethlans_worker_agent.unreal_executor.launch_flags import CommandError
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, LocalConflict
from shared.unreal import shots_result

logger = logging.getLogger(__name__)

EXPORT_PARAMS, EXPORT_RESULT, EXPORT_DIR = 'export_params.json', 'audio_sources.json', 'audio_src'
EXPORT_PHASE, EXPORT_ATTEMPT = 'prep', 2
INVALID_WARNINGS = "The sound export returned an invalid result (warnings); some of its warnings were not read."


def export_warnings(data) -> Tuple[Tuple[str, ...], bool]:
    """``(cleaned warnings, field valid)`` of an export result (``None`` or a dict); deduplicated, in order."""
    raw = data.get('warnings', []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        return (), False
    head = raw[:shots_result.MAX_WARNINGS]
    texts = (queue_prep.sanitize(item, shots_result.WARNING_MAX_CHARS) for item in head if isinstance(item, str))
    return tuple(dict.fromkeys(text for text in texts if text.strip())), all(isinstance(item, str) for item in head)


def _forward(flow, data) -> None:
    """Add the export's warnings to the job's summary, each once (#284)."""
    texts, valid = export_warnings(data)
    if not valid:
        logger.warning("Audio job %s: the sound export's warnings field is invalid.", flow.params.job_id)
        texts += (INVALID_WARNINGS,)
    have = set(flow.summary.warnings())
    for text in texts:
        if text not in have:
            flow.summary.warn(text)
            have.add(text)


def _why(exc) -> str:
    """A reason with no path: the worker's own ``JobFailure`` text, else the exception's class name."""
    return exc.message if isinstance(exc, JobFailure) else type(exc).__name__


def _export(flow, root: Path, export_dir: Path):
    """Run the export commandlet; ``(result or None, wall seconds)``."""
    fs_safe.ensure_owned_dir(export_dir)
    params_path, result_path = root / EXPORT_PARAMS, root / EXPORT_RESULT
    queue_prep.write_json(params_path, sequence_prep.export_params(flow.params, export_dir, result_path,
                                                                   flow.job.scripts))
    launch = flow.builders.build_prep(flow.ctx, flow.uproject, flow.job.script(job_dir.EXPORT_SCRIPT),
                                      flow.job.log(EXPORT_PHASE, EXPORT_ATTEMPT), params_path, flow.params.merged)
    run = flow.runner.launch(EXPORT_PHASE, EXPORT_ATTEMPT, launch,
                             success_check=lambda: queue_prep.result_ok(result_path), sample=False)
    render_check.check_prep(run.outcome)
    return queue_prep.load(result_path), run.outcome.wall_s


def _open(stack, path: Path, folder: Path) -> sound_mix.Source:
    handle = stack.enter_context(fs_safe.open_output_file(path, folder))
    return sound_mix.Source(stack.enter_context(wave.open(handle, 'rb')))


def _sections(stack, rows, export_dir: Path):
    """``sound_mix.Section`` per checked row, each source opened once; ``SourcesError`` names a bad file."""
    sources, out = {}, []
    for index, row in enumerate(rows):
        if row.wav not in sources:
            try:
                sources[row.wav] = _open(stack, export_dir / row.wav, export_dir)
            except (OSError, EOFError, ValueError, wave.Error):
                raise sequence_prep.SourcesError(f'sections[{index}].wav') from None
        source = sources[row.wav]
        if source.rate != row.sample_rate:
            raise sequence_prep.SourcesError(f'sections[{index}].sample_rate')
        out.append(sound_mix.Section(row.start, row.end, row.offset, row.volume, row.looping, source))
    return out


def measure(flow, wav_path: Path, rate_fraction) -> dict:
    """The run's ``offset`` object (``shared.unreal.sound_result``); never raises a job failure."""
    run, (num, den) = flow.params.audio, rate_fraction
    r0, end = run.first, run.end + 1
    root = Path(flow.job.output).parent
    export_dir = root / EXPORT_DIR
    try:
        try:
            data, export_s = _export(flow, root, export_dir)
        except (JobFailure, LocalConflict, CommandError, OSError) as exc:
            logger.warning("Audio job %s: the sound export failed (%s).", flow.params.job_id, exc)
            return sound_offset.unavailable(f"the export step failed ({_why(exc)})")
        _forward(flow, data)
        if isinstance(data, dict) and data.get('ok') is False:
            text = queue_prep.sanitize(data.get('error') or 'no message', 300)
            return sound_offset.unavailable(f"the export step failed: {text}", export_s=export_s)
        try:
            rows, unsupported = sequence_prep.parse_sources(data, r0, end)
            with contextlib.ExitStack() as stack:
                sections = _sections(stack, rows, export_dir)
                try:
                    render = _open(stack, Path(wav_path), Path(wav_path).parent)
                except (OSError, EOFError, ValueError, wave.Error) as exc:
                    return sound_offset.unavailable(f"the render's .wav could not be read ({_why(exc)})",
                                                    unsupported, export_s)
                return sound_offset.measure(render, sections, run.shots, r0, end, num, den, unsupported, export_s)
        except sequence_prep.SourcesError as exc:
            return sound_offset.unavailable(str(exc), export_s=export_s)
    finally:
        fs_safe.remove_tree(export_dir)
