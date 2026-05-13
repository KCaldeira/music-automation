"""Convert note events to a MIDI file using mido."""

from pathlib import Path

import mido

from generator import NoteEvent


def events_to_midi(
    tracks: list[list[NoteEvent]],
    tempo: int,
    output_path: str,
) -> None:
    """Write note events to a Type 1 MIDI file.

    Track 0 contains the tempo meta-message.
    Tracks 1..N contain note data.
    """
    mid = mido.MidiFile(type=1)

    # Track 0: tempo
    tempo_track = mido.MidiTrack()
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo)))
    mid.tracks.append(tempo_track)

    for track_events in tracks:
        track = mido.MidiTrack()

        # Build a flat list of (absolute_tick, message) pairs
        messages = []
        for ev in track_events:
            messages.append((ev.start_tick, "note_on", ev.pitch, ev.velocity))
            messages.append((ev.start_tick + ev.duration_ticks, "note_off", ev.pitch, 0))

        # Sort by absolute time, then note_off before note_on at same tick
        messages.sort(key=lambda m: (m[0], 0 if m[1] == "note_off" else 1))

        prev_tick = 0
        for abs_tick, msg_type, pitch, vel in messages:
            delta = abs_tick - prev_tick
            if msg_type == "note_on":
                track.append(mido.Message("note_on", note=pitch, velocity=vel, time=delta))
            else:
                track.append(mido.Message("note_off", note=pitch, velocity=vel, time=delta))
            prev_tick = abs_tick

        mid.tracks.append(track)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    mid.save(output_path)
