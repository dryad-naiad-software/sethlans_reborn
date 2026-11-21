# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long and how often an audio section's sound plays in UE 5.8.4 (sequence rendering D23, FR-SA-9, #283).

Runs in UE 5.8's embedded Python, loaded by the sound-source export (``sethlans_audio_export.py``) from one fixed
file (``importlib.util.spec_from_file_location``, never through ``sys.path``). Data, not a worker module: it
imports only ``unreal`` and ``os``, and only reads.

* ``loops(sec, sound)``: UE's ``IsLoopingOrRepeating`` (``MovieSceneAudioSection.cpp:415-433``). A wave that is
  not one-shot (its own ``looping``) always loops; a one-shot one loops on the section's ``repeating`` with
  ``Sequencer.Audio.UseRepeating`` on (the default, cpp:30), on its legacy ``looping`` with it off.
* ``keeps_playing(sec, owner, warnings)``: whether a one-shot, non-repeating section's sound plays on past the section's end.
  ``Sequencer.Audio.UsePlayUntilFinished`` on: its play-until-finished flag. Off (the default, cpp:50): its When
  Finished mode; Keep State keeps the sound playing (``MovieSceneAudioSystem.cpp:441-445``), Restore State stops
  it; a mode UE 5.8.4 does not have is unsupported. Project Default means the owning sequence's class default:
  read for a ``LevelSequence`` (with a warning naming the value used), unsupported for any other class.

``DefaultCompletionMode`` is ``UPROPERTY(config)`` only (``MovieSceneSequence.h:404-405``, ``Config=Engine``): not
a Python name, readable on no object. It is read from the config files UE reads, key ``DefaultCompletionMode`` in
``[/Script/LevelSequence.LevelSequence]``: the project's ``Config/DefaultEngine.ini`` first, then the engine's
``Config/BaseEngine.ini`` (which ships ``RestoreState``, ``BaseEngine.ini:3693-3694``). The last ``Key=`` or
``+Key=`` line wins, a ``-Key=`` line removes that value, comments are skipped, duplicate keys are fine. Neither
file setting a known mode fails with fixed text: no value is assumed.
"""
import os

import unreal

UNTIL_CVAR = "Sequencer.Audio.UsePlayUntilFinished"  # off by default (MovieSceneAudioSection.cpp:50)
REPEAT_CVAR = "Sequencer.Audio.UseRepeating"  # on by default (MovieSceneAudioSection.cpp:30)
SECTION, KEY = "[/Script/LevelSequence.LevelSequence]", "DefaultCompletionMode"
CONFIG_FILES = ("DefaultEngine.ini", "BaseEngine.ini")  # the project's, then the engine's
KEEPS = {"KeepState": True, "RestoreState": False}
PROP_MISSING = "Unreal has no property {name} on {cls}; the sound sources were not read."
NO_DEFAULT = ("Neither the project's DefaultEngine.ini nor the engine's BaseEngine.ini sets the level sequences' "
              "DefaultCompletionMode; the sound sources were not read.")
BAD_DEFAULT = ("The level sequences' DefaultCompletionMode in {file} is not KeepState or RestoreState; the sound "
               "sources were not read.")
OWNER_CLASS = "LevelSequence"  # SECTION's class; DefaultCompletionMode is a per-class config value
UNKNOWN_MODE = "an unknown When Finished mode"
OTHER_CLASS = "a project default When Finished mode in a sequence that is not a LevelSequence"
ASSUMED = ("Sound sections with the project default When Finished mode were measured as {value}, the level sequence "
           "default in the config files; a saved sequence can hold its own value, which Sethlans cannot read.")
_CACHE = {}


class SoundRulesError(RuntimeError):
    """A failure whose text this module composed (fixed words and a file or property name): safe to report."""


def prop(obj, name):
    """An editor property; one the class does not have fails with fixed text naming it (never a guess, #283)."""
    try:
        return obj.get_editor_property(name)
    except Exception:
        raise SoundRulesError(PROP_MISSING.format(name=name, cls=type(obj).__name__)) from None


def cvar_on(name):
    return bool(unreal.SystemLibrary.get_console_variable_bool_value(name))


def loops(sec, sound):
    if prop(sound, "looping"):
        return True
    return bool(prop(sec, "repeating" if cvar_on(REPEAT_CVAR) else "looping"))


def ini_value(path, section=SECTION, key=KEY):
    """The value ``key`` ends with in ``section`` of one ini file; ``None`` when the file or key is absent."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            lines = list(handle)
    except OSError:
        return None
    value, inside = None, False
    for line in (raw.strip() for raw in lines):
        if line.startswith("["):
            inside = line.casefold() == section.casefold()
            continue
        name, sep, text = line.partition("=")
        sign = name[:1] if name[:1] in "+-." else ""
        if not inside or not sep or line[:1] in ";#" or name[len(sign):].strip() != key:
            continue
        text = text.strip().strip('"')
        value = (None if text == value else value) if sign == "-" else text
    return value


def sequence_keeps_state():
    """Whether Project Default means Keep State for level sequences, from the config UE reads (cached)."""
    if "keeps" not in _CACHE:
        folders = (unreal.Paths.project_config_dir(), unreal.Paths.engine_config_dir())
        for folder, name in zip(folders, CONFIG_FILES):
            value = ini_value(os.path.join(unreal.Paths.convert_relative_path_to_full(folder), name))
            if value is not None:
                if value not in KEEPS:
                    raise SoundRulesError(BAD_DEFAULT.format(file=name))
                _CACHE["keeps"] = KEEPS[value]
                break
        else:
            raise SoundRulesError(NO_DEFAULT)
    return _CACHE["keeps"]


def keeps_playing(sec, owner, warnings):
    """``True``/``False``: the sound plays on past the section's end or not; a text: why it is unsupported.

    Project Default is the owning sequence's own class default (``MovieSceneCompiledDataManager.cpp:1462``); only
    a ``LevelSequence`` owner's comes from ``SECTION`` (other classes, such as template or plugin sequences, set
    their own), and a saved asset can hold a value Python cannot read, so using it adds ``ASSUMED`` once."""
    if cvar_on(UNTIL_CVAR):
        return bool(sec.get_play_until_finished())
    modes = unreal.MovieSceneCompletionMode
    mode = sec.get_completion_mode()
    if mode == modes.PROJECT_DEFAULT:
        if owner.get_class().get_name() != OWNER_CLASS:
            return OTHER_CLASS
        keeps = sequence_keeps_state()
        text = ASSUMED.format(value="KeepState" if keeps else "RestoreState")
        if text not in warnings:
            warnings.append(text)
        return keeps
    return True if mode == modes.KEEP_STATE else False if mode == modes.RESTORE_STATE else UNKNOWN_MODE
