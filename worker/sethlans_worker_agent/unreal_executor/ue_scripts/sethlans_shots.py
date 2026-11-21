# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shot and camera-cut helpers for the prep script (Unreal foundation FR-UE-8, FR-UE-25; sequence rendering FR-SW-4).

Runs in UE 5.8's embedded Python, loaded by the prep script from one fixed
file (``importlib.util.spec_from_file_location``, never through
``sys.path``). Data, not a worker module: it imports only ``unreal``.

It **only reads** the sequence: shot sections (CinematicShotTrack and
SubTrack), camera cuts, the edit-to-inner frame mapping, the pilot frames
and the shot list of shot discovery (``list_shots``, D4, FR-SD-5, FR-SD-6).
It never sets a property or saves an asset.
"""
import unreal

NAME_MAX = 56  # leaves room for "-part<n>"; the worker cleans and caps every name again (FR-SD-6, FR-SD-7)
SECTION_OVERLAP = "Shot sections {a} and {b} overlap; Sethlans cannot tell which one renders."
CUT_OVERLAP = "Camera cuts {a} and {b} overlap; Sethlans does not support blended camera cuts yet."
MUTE_WARNING = "Mute and disabled state of shot tracks is not read; muted shots are planned."


class ShotsError(RuntimeError):
    """A discovery failure whose text this module composed (fixed words and cleaned names): safe to report."""


def rate(frame_rate):
    return frame_rate.numerator / frame_rate.denominator


def active(section):
    return section.is_active()  # MovieSceneSection.IsActive; never assumed (#283: no silent default)


def _mapped(sec, sub, disp):
    inner_rate = rate(sub.get_display_rate())
    offset = sec.parameters.start_frame_offset.value * inner_rate / rate(sub.get_tick_resolution())
    return sec.get_start_frame(), sec.get_end_frame(), sub, sub.get_playback_start(), offset, inner_rate / disp


def shot_sections(seq):
    """(start, end, sub, inner_start, offset, inner/outer rate ratio) of every enabled shot section.

    Section bounds are edit display frames; the inner frame of edit frame f is
    ``inner_start + offset + (f - start) * ratio`` (S1 mapping, FR-UE-8 step 5).
    """
    disp = rate(seq.get_display_rate())
    out = []
    for track in seq.get_tracks():
        if not isinstance(track, (unreal.MovieSceneCinematicShotTrack, unreal.MovieSceneSubTrack)):
            continue
        for sec in track.get_sections():
            sub = sec.get_sequence()
            if sub is not None and active(sec):
                out.append(_mapped(sec, sub, disp))
    return out


def cuts_of(seq):
    """Enabled camera-cut sections ``(start, end)`` of a sequence's own camera-cut track."""
    cuts = []
    for track in seq.get_tracks():
        if isinstance(track, unreal.MovieSceneCameraCutTrack):
            cuts += [(s.get_start_frame(), s.get_end_frame()) for s in track.get_sections() if active(s)]
    return cuts


def in_shot_cut(shot, frame):
    start, end, sub, inner_start, offset, ratio = shot
    inner = inner_start + offset + (frame - start) * ratio
    return start <= frame < end and any(cs <= inner < ce for cs, ce in cuts_of(sub))


def cut_starts(seq):
    """Edit frames at which a camera cut begins (own cuts, else via the shots)."""
    cuts = cuts_of(seq)
    if cuts:
        return sorted({s for s, _e in cuts})
    starts = set()
    for start, _end, sub, inner_start, offset, ratio in shot_sections(seq):
        for cs, _ce in cuts_of(sub):
            starts.add(int(round(max(start, start + (cs - inner_start - offset) / ratio))))
    return sorted(starts)


def frame_in_cut(seq, frame):
    cuts = cuts_of(seq)
    if cuts:
        return any(s <= frame < e for s, e in cuts)
    return any(in_shot_cut(shot, frame) for shot in shot_sections(seq))


def pilot_frames(seq, first, last):
    """FR-UE-25: the first qualifying frame of every cut/shot overlapping [first, last)."""
    frames = set()
    cuts = cuts_of(seq)
    if cuts:
        for s, e in cuts:
            if e > first and s < last:
                frames.add(max(s, first))
        return sorted(frames)
    for shot in shot_sections(seq):
        start, end = shot[0], shot[1]
        lo, hi = max(start, first), min(end, last)
        steps = 0
        for f in range(lo, hi):
            steps += 1
            if steps > 1000000:
                break
            if in_shot_cut(shot, f):
                frames.add(f)
                break
    return sorted(frames)


def clean(text):
    """FR-SD-6's character rule and a first length cap (the worker applies every name rule again)."""
    return "".join(c if c.isascii() and (c.isalnum() or c in "_.-") else "_" for c in str(text)[:NAME_MAX])


def _name_of(sub):
    return clean(sub.get_name())  # an empty name: the worker names it shot_<nnn>


def _first_at(start, base, ratio, bound):
    """The first edit frame f whose inner frame ``base + (f - start) * ratio`` (``in_shot_cut``) is >= ``bound``.

    One estimate, then a few unit steps on the same comparison, so float rounding agrees with the per-frame rule
    and no loop runs over the frames (D4: interval arithmetic).
    """
    f = -int(-(start + (bound - base) / ratio) // 1)
    for _ in range(4):
        if base + (f - 1 - start) * ratio >= bound:
            f -= 1
    for _ in range(4):
        if base + (f - start) * ratio < bound:
            f += 1
    return f


def _merge(spans):
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)  # touching or overlapping cuts leave no hole
        else:
            out.append([lo, hi])
    return out


def _play_rate(sec):
    """The section's fixed play rate, or None when unreadable (the caller then fails the discovery loudly).

    UE 5.8.4: ``parameters.time_scale`` is a ``MovieSceneTimeWarpVariant``; ``to_fixed_play_rate()`` reads it
    (probe on 5.8.4). The variant offers no "is fixed" check, and a curve warp reads as 1.0 (the engine logs an
    error), so a curve is not detected here.
    """
    try:
        return float(sec.get_editor_property("parameters").get_editor_property("time_scale").to_fixed_play_rate())
    except Exception:
        return None


def _nested_cuts(sub):
    """Whether a subsequence inside the shot has camera cuts of its own (MRQ uses them; they are not read here)."""
    kinds = (unreal.MovieSceneSubTrack, unreal.MovieSceneCinematicShotTrack)
    return any(s.get_sequence() is not None and cuts_of(s.get_sequence())
               for t in sub.get_tracks() if isinstance(t, kinds) for s in t.get_sections())


def discovery_sections(seq, warnings):
    """D4's shot sections: the enabled sections of the top-level cinematic shot tracks.

    A section whose play rate is not 1, or cannot be read, is refused (the edit-to-inner mapping assumes 1). Track
    mute and row eval-disabled state are not readable from Python in 5.8.4 (probe), so muted shots are planned:
    one warning says so. MRQ also takes camera cuts from nested subsequences and from the sequence's own camera-cut
    track, which are not read here: each case gets a warning.
    """
    disp, out = rate(seq.get_display_rate()), []
    for track in seq.get_tracks():
        if not isinstance(track, unreal.MovieSceneCinematicShotTrack):
            continue
        for sec in track.get_sections():
            sub = sec.get_sequence()
            if sub is None or not active(sec):
                continue
            name, play_rate = _name_of(sub), _play_rate(sec)
            if play_rate is None:
                raise ShotsError(f"The time scale of shot {name} could not be read; Sethlans needs 1.")
            if play_rate != 1.0:
                raise ShotsError(f"Shot {name} plays at a time scale other than 1; Sethlans needs 1.")
            if _nested_cuts(sub):
                warnings.append(f"Shot {name} has subsequences with camera cuts; those cuts are not read.")
            out.append(_mapped(sec, sub, disp))
    if out:
        warnings.append(MUTE_WARNING)
    if out and cuts_of(seq):
        warnings.append("The sequence has camera cuts of its own beside its shot tracks; they are not read.")
    return out


def _section_rows(sections, first, last, warnings):
    """D4: each section clipped to its inner camera-cut coverage and the playback range; a hole splits it."""
    rows = []
    for start, end, sub, inner_start, offset, ratio in sections:
        lo_edit, hi_edit = max(start, first), min(end, last)
        if lo_edit >= hi_edit:
            continue  # outside the playback range: never rendered
        name = _name_of(sub)
        if not ratio > 0:
            raise ShotsError(f"Shot {name} has an invalid frame rate.")
        base = inner_start + offset
        spans = [(max(lo_edit, _first_at(start, base, ratio, cs)), min(hi_edit, _first_at(start, base, ratio, ce)))
                 for cs, ce in cuts_of(sub)]
        cover = _merge(span for span in spans if span[0] < span[1])
        if not cover:
            warnings.append(f"Shot {name} has no camera cut inside the playback range; its frames are not rendered.")
            continue
        for part, (lo, hi) in enumerate(cover, 1):
            rows.append([name if part == 1 else f"{name}-part{part}", int(lo), int(hi)])
    return rows


def _cut_rows(seq, first, last):
    spans = sorted((max(s, first), min(e, last)) for s, e in cuts_of(seq))
    return [[f"cut_{i:03d}", int(lo), int(hi)] for i, (lo, hi) in enumerate((s for s in spans if s[0] < s[1]), 1)]


def _no_overlap(rows, message):
    rows.sort(key=lambda row: (row[1], row[2]))
    for a, b in zip(rows, rows[1:]):  # sorted by start: any overlap shows between neighbours
        if b[1] < a[2]:
            raise ShotsError(message.format(a=a[0], b=b[0]))
    return rows


def list_shots(seq, first, last, warnings):
    """D4: ``(shot_source, [[name, start, end], ...])`` in edit order, end exclusive; raises when there is none.

    Shot tracks first (``discovery_sections``, each clipped to its inner camera cuts), else the top-level camera
    cuts; both clipped to the playback range ``[first, last)``. Overlap is checked on the **sections** clipped to
    the playback range, not on the shots: where rows overlap Sequencer renders only the top one, whatever the cuts.
    """
    sections = discovery_sections(seq, warnings)
    if sections:
        _no_overlap([[_name_of(s[2]), max(s[0], first), min(s[1], last)] for s in sections
                     if max(s[0], first) < min(s[1], last)], SECTION_OVERLAP)
        rows = _section_rows(sections, first, last, warnings)
        if not rows:
            raise ShotsError("Shot discovery failed: no shot section has a camera cut inside the playback range.")
        return "shot_tracks", sorted(rows, key=lambda row: row[1])
    rows = _cut_rows(seq, first, last)
    if not rows:
        raise ShotsError("Shot discovery failed: no shot track or camera cut found inside the playback range.")
    return "camera_cuts", _no_overlap(rows, CUT_OVERLAP)
