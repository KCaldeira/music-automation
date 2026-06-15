"""Convert internal Track representation to a Type 1 MIDI file via mido."""

from dataclasses import dataclass
from pathlib import Path

import mido


PPQN = 960   # ticks per quarter note. Divisible by 2, 3, 4, 5, 6, 8.


@dataclass
class NoteEvent:
    start_tick: int
    duration_ticks: int
    pitch: int
    velocity: int = 100


def track_to_note_events(track, steps_per_cycle: int, ticks_per_step: int):
    """Flatten a Track (list[list[StepEvent]]) into a flat list of NoteEvents.

    Cycle k's events occupy ticks [k * steps_per_cycle * ticks_per_step,
    (k+1) * steps_per_cycle * ticks_per_step). Rests (pitch is None) are dropped.
    """
    notes: list[NoteEvent] = []
    for cycle_idx, cycle in enumerate(track):
        cycle_base_tick = cycle_idx * steps_per_cycle * ticks_per_step
        for ev in cycle:
            if ev.pitch is None:
                continue
            notes.append(NoteEvent(
                start_tick=cycle_base_tick + ev.start_step * ticks_per_step,
                duration_ticks=ev.duration_steps * ticks_per_step,
                pitch=ev.pitch,
            ))
    return notes


def write_midi(tracks, output_path: str, track_names=None):
    """Write a Type 1 MIDI file: tracks 0..N-1 = notes (channel 0).

    If track_names is given, it must be parallel to tracks; each name is emitted
    as a track_name meta message at the start of the corresponding track.
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQN)

    for i, track_events in enumerate(tracks):
        track = mido.MidiTrack()

        if track_names is not None:
            track.append(mido.MetaMessage("track_name", name=track_names[i], time=0))

        messages = []
        for ev in track_events:
            messages.append((ev.start_tick, "note_on", ev.pitch, ev.velocity))
            messages.append((ev.start_tick + ev.duration_ticks, "note_off", ev.pitch, 0))

        # Sort by absolute tick, ties: note_off before note_on (release before re-attack).
        messages.sort(key=lambda m: (m[0], 0 if m[1] == "note_off" else 1))

        prev_tick = 0
        for abs_tick, msg_type, pitch, vel in messages:
            delta = abs_tick - prev_tick
            track.append(mido.Message(msg_type, note=pitch, velocity=vel, time=delta))
            prev_tick = abs_tick

        mid.tracks.append(track)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    mid.save(output_path)
