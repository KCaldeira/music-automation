"""Elaboration cycle-generation algorithm.

Builds music by progressively elaborating a single sustained base_pitch note.
Cycle 0 is that one note; each later forward cycle is the previous cycle plus
`changes_per_cycle` edits (purely cumulative). By default the ORDER OF THE CYCLES
is reversed at output, so the piece starts complex and converges to one held
note. See plan.txt / execution_plan.md for the full spec.

No MIDI / file I/O lives here. Pitch selection reuses generator.sample_pitch and
generator.build_pitch_lists; output StepEvents reuse generator.StepEvent.
"""

import copy
from dataclasses import dataclass

import numpy as np

import generator


# Division kinds.
START, SUSTAIN, REST = 0, 1, 2


@dataclass
class Grid:
    """Per-division representation of one cycle.

    kind[d]  in {START, SUSTAIN, REST}
    pitch[d] = the sounding pitch on START and its SUSTAIN divisions; None on REST.
    Invariant: a SUSTAIN at d continues the note whose START is the nearest
    earlier non-SUSTAIN; index 0 is always START or REST (never SUSTAIN).
    """
    kind: list
    pitch: list


def init_cycle(divisions_per_cycle, base_pitch):
    """One note at base_pitch sustained over the whole cycle."""
    kind = [SUSTAIN] * divisions_per_cycle
    kind[0] = START
    pitch = [base_pitch] * divisions_per_cycle
    return Grid(kind=kind, pitch=pitch)


def is_sounding(grid, d):
    return 0 <= d < len(grid.kind) and grid.kind[d] in (START, SUSTAIN)


def nearest_prev_pitch(grid, d, base_pitch):
    """Pitch of the nearest note sounding before division d, else base_pitch."""
    j = d - 1
    while j >= 0:
        if grid.kind[j] in (START, SUSTAIN):
            return grid.pitch[j]
        j -= 1
    return base_pitch


def note_start_index(grid, d):
    """START index of the note sounding at d (d must be START or SUSTAIN)."""
    j = d
    while j > 0 and grid.kind[j] == SUSTAIN:
        j -= 1
    return j


def note_end(grid, d):
    """Last division index of the note sounding at d (d must be START or SUSTAIN)."""
    n = len(grid.kind)
    j = d + 1
    while j < n and grid.kind[j] == SUSTAIN:
        j += 1
    return j - 1


def _make_rest_run(grid, d):
    """Turn the note sounding at d, from d through its end, into a REST run."""
    end = note_end(grid, d)
    for j in range(d, end + 1):
        grid.kind[j] = REST
        grid.pitch[j] = None


def _sample_pitch(grid, d, cfg, pitch_lists, rng, exclude_pitch=None):
    prev = nearest_prev_pitch(grid, d, cfg["base_pitch"])
    u = rng.random()
    return generator.sample_pitch(
        prev, pitch_lists[0], pitch_lists[1], cfg["base_pitch"],
        cfg["interval_gravity"], cfg["pitch_gravity"], u,
        exclude_pitch=exclude_pitch,
    )


def forward_extension(grid, note_start_d, cfg, rng):
    """Absorb following REST divisions into the note starting at note_start_d.

    Walk forward from the note's current end+1: stop at a note START or the cycle
    end; over a REST, absorb it with probability division_extension_probability
    at that division, else stop.
    """
    n = len(grid.kind)
    pitch = grid.pitch[note_start_d]
    ext = cfg["division_extension_probability"]
    j = note_end(grid, note_start_d) + 1
    while j < n and grid.kind[j] == REST:
        if rng.random() < ext[j]:
            grid.kind[j] = SUSTAIN
            grid.pitch[j] = pitch
            j += 1
        else:
            break


def classify_and_apply(grid, d, cfg, pitch_lists, rng):
    """Apply one elaboration edit at division d (cases a / b / c)."""
    k = grid.kind[d]

    if k == START:                                          # case (a): note start
        if rng.random() < cfg["division_rest_probability"][d]:
            _make_rest_run(grid, d)                         # whole note -> rest
        else:
            new_pitch = _sample_pitch(grid, d, cfg, pitch_lists, rng,
                                      exclude_pitch=grid.pitch[d])   # must change
            for j in range(d, note_end(grid, d) + 1):
                grid.pitch[j] = new_pitch

    elif k == REST:                                         # case (b): rest
        if is_sounding(grid, d - 1) and rng.random() < cfg["division_extension_probability"][d]:
            ps = note_start_index(grid, d - 1)              # extend preceding note
            grid.kind[d] = SUSTAIN
            grid.pitch[d] = grid.pitch[ps]
            forward_extension(grid, ps, cfg, rng)
        else:                                               # start a new note (same pitch allowed)
            grid.kind[d] = START
            grid.pitch[d] = _sample_pitch(grid, d, cfg, pitch_lists, rng)
            forward_extension(grid, d, cfg, rng)

    else:                                                   # case (c): mid-note (SUSTAIN)
        if rng.random() < cfg["division_rest_probability"][d]:
            _make_rest_run(grid, d)                         # second half -> rest
        else:
            grid.kind[d] = START                            # re-articulate same pitch


def pick_division(cfg, rng):
    """Sample a division index proportional to division_change_probability."""
    w = np.asarray(cfg["division_change_probability"], dtype=float)
    cum = np.cumsum(w / w.sum())
    return int(np.searchsorted(cum, rng.random()))


def generate_track(cfg, rng):
    """Build one track's list of Grid cycles (reversed if reverse_cycle_order).

    Cycle 0 is the single sustained base_pitch note; each subsequent cycle is the
    previous one with `changes_per_cycle` more edits applied (cumulative).
    """
    dpc = cfg["divisions_per_beat"] * cfg["beats_per_bar"] * cfg["bars_per_cycle"]
    pitch_lists = generator.build_pitch_lists(
        cfg["base_pitch"], cfg["max_pitch_range"], cfg["note_probability"])

    grid = init_cycle(dpc, cfg["base_pitch"])
    cycles = [copy.deepcopy(grid)]
    for _ in range(cfg["total_cycles"] - 1):
        for _ in range(cfg["changes_per_cycle"]):
            d = pick_division(cfg, rng)
            classify_and_apply(grid, d, cfg, pitch_lists, rng)
        cycles.append(copy.deepcopy(grid))

    if cfg["reverse_cycle_order"]:
        cycles.reverse()
    return cycles


def grid_to_stepevents(grid):
    """Convert one Grid into a list of generator.StepEvent (notes + rest runs)."""
    n = len(grid.kind)
    events = []
    d = 0
    while d < n:
        if grid.kind[d] == START:
            end = note_end(grid, d)
            events.append(generator.StepEvent(
                pitch=int(grid.pitch[d]), start_step=d, duration_steps=end - d + 1))
            d = end + 1
        elif grid.kind[d] == REST:
            j = d
            while j < n and grid.kind[j] == REST:
                j += 1
            events.append(generator.StepEvent(
                pitch=None, start_step=d, duration_steps=j - d))
            d = j
        else:  # defensive: a leading SUSTAIN should never occur (index 0 invariant)
            d += 1
    return events
