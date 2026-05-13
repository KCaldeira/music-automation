"""Pure cycle-generation algorithm — Steps 1-5 from README plus per-cycle reversal.

No MIDI / file I/O lives here.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class StepEvent:
    pitch: int | None        # None == rest (preserves duration, emits no MIDI)
    start_step: int          # within its own cycle, 0 .. steps_per_cycle - 1
    duration_steps: int


Track = list[list[StepEvent]]


def expand_weights(cfg: dict):
    """README Step 1. Returns (note_pitch_list, note_probability_list, beat_start_list, steps_per_cycle)."""
    steps_per_bar = cfg["divisions_per_beat"] * cfg["beats_per_bar"]
    steps_per_cycle = steps_per_bar * cfg["bars_per_cycle"]

    beat_start_list = np.tile(
        np.asarray(cfg["beat_start_probability"], dtype=float),
        cfg["bars_per_cycle"],
    )

    base = cfg["base_pitch"]
    r = cfg["max_pitch_range"]
    note_pitch_list = np.arange(base - r, base + r + 1)
    note_prob = np.asarray(cfg["note_probability"], dtype=float)
    note_probability_list = note_prob[(note_pitch_list - base) % 12]

    return note_pitch_list, note_probability_list, beat_start_list, steps_per_cycle


def sample_pitch(prev_pitch, note_pitch_list, note_probability_list,
                 base_pitch, interval_gravity, pitch_gravity, rng):
    """README Step 2."""
    raw = (
        note_probability_list
        * np.exp(-(note_pitch_list - prev_pitch) ** 2 / interval_gravity ** 2)
        * np.exp(-(note_pitch_list - base_pitch) ** 2 / pitch_gravity ** 2)
    )
    cum = np.cumsum(raw / raw.sum())
    idx = int(np.searchsorted(cum, rng.random()))
    return int(note_pitch_list[idx])


def sample_next_start(current_step, beat_start_list, steps_per_cycle,
                      step_length_scale, rng):
    """README Step 3. Returns next_note_start in [current_step+1, steps_per_cycle].

    The value steps_per_cycle means "end of cycle" (current note fills the rest).
    """
    ext = np.empty(steps_per_cycle + 1, dtype=float)
    ext[:steps_per_cycle] = beat_start_list
    ext[steps_per_cycle] = beat_start_list[0]            # periodic extension placeholder

    eliminated = ext[: current_step + 1].sum()
    ext[: current_step + 1] = 0.0
    ext[steps_per_cycle] = eliminated                    # recycle masked mass

    step_number = np.arange(steps_per_cycle + 1)
    raw = ext * np.exp(-(step_number - current_step) ** 2 / step_length_scale ** 2)
    cum = np.cumsum(raw / raw.sum())
    return int(np.searchsorted(cum, rng.random()))


def should_terminate(pitch, current_step, beat_start_list, max_attractiveness,
                     note_probability, base_pitch, steps_per_cycle,
                     pitch_gravity, ending_gravity, rng):
    """README Step 5."""
    pitch_weight = note_probability[(pitch - base_pitch) % 12]
    step_weight = float(beat_start_list[current_step])
    p_terminate = (
        (pitch_weight * step_weight / max_attractiveness)
        * np.exp(-((steps_per_cycle - current_step) ** 2) / ending_gravity ** 2)
        * np.exp(-((pitch - base_pitch) ** 2) / pitch_gravity ** 2)
    )
    return rng.random() < p_terminate


def generate_cycle(cfg, note_pitch_list, note_probability_list, beat_start_list,
                   steps_per_cycle, max_attractiveness, rng):
    """Generate one cycle's event list. Returns (events, terminated)."""
    events: list[StepEvent] = []
    current_step = 0
    prev_pitch = cfg["base_pitch"]
    terminated = False

    while True:
        pitch = sample_pitch(
            prev_pitch, note_pitch_list, note_probability_list,
            cfg["base_pitch"], cfg["interval_gravity"], cfg["pitch_gravity"], rng,
        )
        next_start = sample_next_start(
            current_step, beat_start_list, steps_per_cycle,
            cfg["step_length_scale"], rng,
        )
        events.append(StepEvent(
            pitch=pitch,
            start_step=current_step,
            duration_steps=next_start - current_step,
        ))

        if should_terminate(
            pitch, current_step, beat_start_list, max_attractiveness,
            cfg["note_probability"], cfg["base_pitch"], steps_per_cycle,
            cfg["pitch_gravity"], cfg["ending_gravity"], rng,
        ):
            terminated = True
            break

        if next_start == steps_per_cycle:
            break

        current_step = next_start
        prev_pitch = pitch

    return events, terminated


def apply_rest_sweep(events, cfg, rng):
    """README Step 4. In-place: each non-rest event may be flipped to a rest."""
    rp = cfg["rest_probability"]
    if rp <= 0:
        return
    max_attract = max(cfg["note_probability"]) * max(cfg["beat_start_probability"])
    steps_per_bar = cfg["divisions_per_beat"] * cfg["beats_per_bar"]
    base = cfg["base_pitch"]
    for ev in events:
        if ev.pitch is None:
            continue
        pw = cfg["note_probability"][(ev.pitch - base) % 12]
        sw = cfg["beat_start_probability"][ev.start_step % steps_per_bar]
        p_rest = rp * (1 - (pw * sw) / max_attract)
        if rng.random() < p_rest:
            ev.pitch = None


def generate_track(cfg, rng):
    """Generate one track (sequence of cycles). Returns (Track, terminated_early)."""
    npl, nplp, bsl, spc = expand_weights(cfg)
    max_attract = max(cfg["note_probability"]) * max(cfg["beat_start_probability"])

    track: Track = []
    terminated_early = False
    for _ in range(cfg["total_cycles"]):
        events, terminated = generate_cycle(cfg, npl, nplp, bsl, spc, max_attract, rng)
        apply_rest_sweep(events, cfg, rng)
        track.append(events)
        if terminated:
            terminated_early = True
            break

    return track, terminated_early


def reverse_track(track: Track) -> Track:
    """Per-cycle time reversal. Pure function — events are recreated, not mutated."""
    reversed_track: Track = []
    for cycle in track:
        new_cycle: list[StepEvent] = []
        step = 0
        for ev in reversed(cycle):
            new_cycle.append(StepEvent(
                pitch=ev.pitch,
                start_step=step,
                duration_steps=ev.duration_steps,
            ))
            step += ev.duration_steps
        reversed_track.append(new_cycle)
    return reversed_track
