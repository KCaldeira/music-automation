"""Core generation algorithms: pitch, rhythm, and volume."""

from dataclasses import dataclass

import numpy as np

from tables import Tables


@dataclass
class NoteEvent:
    start_tick: int
    duration_ticks: int
    pitch: int
    velocity: int


def next_pitch(
    pitch_current: int,
    base_pitch: int,
    pitch_gravity: float,
    tables: Tables,
    rng: np.random.Generator,
) -> int:
    """Determine next pitch using augmented interval probability table."""
    interval_prob = tables.interval_probability_table
    pitch_prob = tables.pitch_probability_table

    augmented = np.zeros(25)
    for i in range(25):
        offset = i - 12 + pitch_current - base_pitch
        pitch_class = offset % 12
        augmented[i] = interval_prob[i] * pitch_prob[pitch_class]

    # Apply gravity
    for i in range(25):
        offset = i - 12 + pitch_current - base_pitch
        gravity_factor = 1.0 - offset / pitch_gravity
        augmented[i] = max(0.0, augmented[i] * gravity_factor)

    total = augmented.sum()
    if total == 0:
        return base_pitch

    augmented /= total
    sampled_index = rng.choice(25, p=augmented)
    new_pitch = pitch_current + (sampled_index - 12)

    # Clamp to MIDI range
    return int(np.clip(new_pitch, 24, 108))


def note_volume(
    pitch_current: int,
    cycle_loc: int,
    volume_variability: float,
    tables: Tables,
    rng: np.random.Generator,
) -> int:
    """Compute MIDI velocity (0-127) from volume formula."""
    loc_vol = tables.location_volume[cycle_loc]
    p_vol = tables.pitch_volume[pitch_current % 12]
    noise = 1.0 + volume_variability * rng.standard_normal()
    vol = loc_vol * p_vol * noise
    vol = min(1.0, max(0.0, vol))
    return int(round(vol * 127))


def generate_track(
    config: dict,
    tables: Tables,
    rng: np.random.Generator,
) -> list[NoteEvent]:
    """Generate a sequence of note events for one track."""
    divisions_per_cycle = (
        config["divisions_per_beat"]
        * config["beats_per_bar"]
        * config["bars_per_cycle"]
    )
    total_divisions = config["total_cycles"] * divisions_per_cycle
    divisions_per_bar = config["divisions_per_beat"] * config["beats_per_bar"]
    ticks_per_division = 480 // config["divisions_per_beat"]

    pitch_current = config["base_pitch"]
    current_location = 0
    events = []

    while current_location < total_divisions:
        cycle_loc = current_location % divisions_per_cycle
        note_prob = tables.note_probability_table[cycle_loc]

        is_note = rng.random() < note_prob

        if is_note:
            # Sample note length
            length_probs = tables.note_length_table[cycle_loc]
            duration = rng.choice(divisions_per_bar - 1, p=length_probs) + 1

            # Determine pitch and volume
            pitch_current = next_pitch(
                pitch_current,
                config["base_pitch"],
                config["pitch_gravity"],
                tables,
                rng,
            )
            velocity = note_volume(
                pitch_current,
                cycle_loc,
                config["volume_variability"],
                tables,
                rng,
            )

            # Truncate if past end
            if current_location + duration > total_divisions:
                duration = total_divisions - current_location

            events.append(NoteEvent(
                start_tick=current_location * ticks_per_division,
                duration_ticks=duration * ticks_per_division,
                pitch=pitch_current,
                velocity=velocity,
            ))
        else:
            # Sample rest length
            length_probs = tables.rest_length_table[cycle_loc]
            duration = rng.choice(divisions_per_bar - 1, p=length_probs) + 1

        current_location += duration

    return events
