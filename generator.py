"""Pure cycle-generation algorithm — Steps 1-5 from README plus per-cycle reversal.

No MIDI / file I/O lives here. All randomness for one cycle is pre-drawn into a
single (NUM_RND_ROWS, steps_per_cycle) array of uniforms in [0, 1); leaf helpers
take a scalar `u`, and the rest sweep takes a 1-D slice.
"""

from dataclasses import dataclass

import numpy as np


# Row indices into the (NUM_RND_ROWS, steps_per_cycle) cycle_randoms array.
RND_PITCH      = 0
RND_NEXT_START = 1
RND_TERMINATE  = 2
RND_REST       = 3
NUM_RND_ROWS   = 4


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
                 base_pitch, interval_gravity, pitch_gravity, u):
    """README Step 2. `u` is a single uniform[0, 1) random."""
    raw = (
        note_probability_list
        * np.exp(-(note_pitch_list - prev_pitch) ** 2 / interval_gravity ** 2)
        * np.exp(-(note_pitch_list - base_pitch) ** 2 / pitch_gravity ** 2)
    )
    cum = np.cumsum(raw / raw.sum())
    idx = int(np.searchsorted(cum, u))
    return int(note_pitch_list[idx])


def sample_next_start(current_step, beat_start_list, steps_per_cycle,
                      step_length_scale, u):
    """README Step 3. `u` is a single uniform[0, 1) random.

    Returns next_note_start in [current_step+1, steps_per_cycle]. The value
    steps_per_cycle means "end of cycle" (current note fills the rest).
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
    return int(np.searchsorted(cum, u))


def should_terminate(pitch, current_step, beat_start_list, max_attractiveness,
                     note_probability, base_pitch, steps_per_cycle,
                     pitch_gravity, ending_gravity, u):
    """README Step 5. `u` is a single uniform[0, 1) random."""
    pitch_weight = note_probability[(pitch - base_pitch) % 12]
    step_weight = float(beat_start_list[current_step])
    p_terminate = (
        (pitch_weight * step_weight / max_attractiveness)
        * np.exp(-((steps_per_cycle - current_step) ** 2) / ending_gravity ** 2)
        * np.exp(-((pitch - base_pitch) ** 2) / pitch_gravity ** 2)
    )
    return u < p_terminate


def apply_rest_sweep(events, cfg, u_rest):
    """README Step 4. In-place. `u_rest` is a 1-D array of uniforms; its first
    `len(events)` entries are consumed (one per event)."""
    rp = cfg["rest_probability"]
    if rp <= 0:
        return
    max_attract = max(cfg["note_probability"]) * max(cfg["beat_start_probability"])
    steps_per_bar = cfg["divisions_per_beat"] * cfg["beats_per_bar"]
    base = cfg["base_pitch"]
    for i, ev in enumerate(events):
        if ev.pitch is None:
            continue
        pw = cfg["note_probability"][(ev.pitch - base) % 12]
        sw = cfg["beat_start_probability"][ev.start_step % steps_per_bar]
        p_rest = rp * (1 - (pw * sw) / max_attract)
        if u_rest[i] < p_rest:
            ev.pitch = None


def generate_cycle(cfg, note_pitch_list, note_probability_list, beat_start_list,
                   steps_per_cycle, max_attractiveness, cycle_randoms):
    """Generate one cycle's event list and apply the rest sweep. Returns (events, terminated).

    `cycle_randoms` is an ndarray of shape (NUM_RND_ROWS, steps_per_cycle) with
    values in [0, 1). Row indices: RND_PITCH, RND_NEXT_START, RND_TERMINATE, RND_REST.
    """
    events: list[StepEvent] = []
    current_step = 0
    prev_pitch = cfg["base_pitch"]
    terminated = False
    note_idx = 0

    while True:
        if note_idx == 0 and cfg["start_cycle_on_base_pitch"]:
            pitch = cfg["base_pitch"]
        else:
            pitch = sample_pitch(
                prev_pitch, note_pitch_list, note_probability_list,
                cfg["base_pitch"], cfg["interval_gravity"], cfg["pitch_gravity"],
                cycle_randoms[RND_PITCH, note_idx],
            )
        next_start = sample_next_start(
            current_step, beat_start_list, steps_per_cycle,
            cfg["step_length_scale"],
            cycle_randoms[RND_NEXT_START, note_idx],
        )
        events.append(StepEvent(
            pitch=pitch,
            start_step=current_step,
            duration_steps=next_start - current_step,
        ))

        if should_terminate(
            pitch, current_step, beat_start_list, max_attractiveness,
            cfg["note_probability"], cfg["base_pitch"], steps_per_cycle,
            cfg["pitch_gravity"], cfg["ending_gravity"],
            cycle_randoms[RND_TERMINATE, note_idx],
        ):
            terminated = True
            break

        if next_start == steps_per_cycle:
            break

        current_step = next_start
        prev_pitch = pitch
        note_idx += 1

    apply_rest_sweep(events, cfg, cycle_randoms[RND_REST])
    return events, terminated


def generate_track(cfg, rng):
    """Generate one track of total_cycles cycles. Returns (Track, num_cycles_terminated_early).

    Step 5 termination only ends the current cycle early; the loop still runs for the
    full total_cycles count.

    Random numbers for each cycle live in a (NUM_RND_ROWS, steps_per_cycle) array.
    The first cycle's array is drawn fresh. Between cycles, each cell is independently
    re-drawn with probability `random_number_change_probability`; the others are
    inherited from the previous cycle. At p=1.0 every cycle is fully fresh (current
    behavior); at p=0.0 every cycle is identical; intermediate values give gradual
    drift across the track.
    """
    npl, nplp, bsl, spc = expand_weights(cfg)
    max_attract = max(cfg["note_probability"]) * max(cfg["beat_start_probability"])
    p_change = cfg["random_number_change_probability"]
    shape = (NUM_RND_ROWS, spc)

    cycle_randoms = rng.random(size=shape)            # fresh start for this track

    track: Track = []
    num_terminated = 0
    for cycle_idx in range(cfg["total_cycles"]):
        events, terminated = generate_cycle(
            cfg, npl, nplp, bsl, spc, max_attract, cycle_randoms,
        )
        track.append(events)
        if terminated:
            num_terminated += 1

        if cycle_idx < cfg["total_cycles"] - 1:
            if p_change >= 1.0:
                cycle_randoms = rng.random(size=shape)
            elif p_change > 0.0:
                mask = rng.random(size=shape) < p_change
                fresh = rng.random(size=shape)
                cycle_randoms = np.where(mask, fresh, cycle_randoms)
            # p_change == 0.0: keep cycle_randoms unchanged

    return track, num_terminated


def reverse_track(track: Track, steps_per_cycle: int,
                  last_start_offset: int = 0) -> Track:
    """Per-cycle time reversal as a true mirror image around the cycle midpoint.

    An event at (start_step=s, duration=d) becomes (start_step=steps_per_cycle-s-d,
    duration=d), so trailing empty space (e.g. from Step-5 early termination) in the
    forward cycle becomes leading empty space in the reversed cycle. Rest events
    (pitch=None) mirror by the same rule.

    When ``last_start_offset`` is negative, the entire reversed cycle is shifted
    so the LAST event starts at step ``steps_per_cycle + last_start_offset``,
    then every event is clipped to the in-cycle range ``[0, steps_per_cycle)``.
    Events that fall entirely outside that range are dropped. With the default
    value of 0 the shift step is skipped and the pure mirror is returned.
    """
    S = steps_per_cycle
    reversed_track: Track = []
    for cycle in track:
        new_cycle = [
            StepEvent(
                pitch=ev.pitch,
                start_step=S - ev.start_step - ev.duration_steps,
                duration_steps=ev.duration_steps,
            )
            for ev in reversed(cycle)
        ]
        if last_start_offset < 0 and new_cycle:
            delta = (S + last_start_offset) - new_cycle[-1].start_step
            clipped = []
            for ev in new_cycle:
                ns = ev.start_step + delta
                ne = ns + ev.duration_steps
                ns_c = max(0, ns)
                ne_c = min(S, ne)
                nd_c = ne_c - ns_c
                if nd_c > 0:
                    clipped.append(StepEvent(
                        pitch=ev.pitch,
                        start_step=ns_c,
                        duration_steps=nd_c,
                    ))
            new_cycle = clipped
        reversed_track.append(new_cycle)
    return reversed_track
