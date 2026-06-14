# Execution Plan — Elaboration Workflow

Derived from `plan.txt`. This is the implementation blueprint for review BEFORE
coding. Nothing here is built yet.

Target layout (already reorganized; new items marked NEW):

```
src/
  config.py        (extend: add elaboration loader + shared helpers)
  generator.py     (small refactor: factor out pitch-list builder; reuse sample_pitch)
  midi_writer.py   (reuse unchanged)
  elaborator.py    NEW  (the elaboration algorithm)
scripts/
  generate_stochastic.py
  generate_elaboration.py   NEW  (thin CLI)
config/
  elaboration/test.json     NEW  (test config)
```

---

## 1. `src/generator.py` — small refactor (no behavior change)

The elaboration pathway reuses `sample_pitch`, but `expand_weights` also reads
`cfg["division_start_probability"]`, which elaboration configs do NOT have.
Factor the pitch-list construction out so both pathways can build the candidate
pitch arrays without needing division_start_probability.

NEW function:
```python
def build_pitch_lists(base_pitch, max_pitch_range, note_probability):
    """Return (note_pitch_list, note_probability_list).
    note_pitch_list   = arange(base-r, base+r+1)
    note_probability_list[i] = note_probability[(pitch_i - base) % 12]
    """
```
Refactor `expand_weights` to call `build_pitch_lists(...)` internally (identical
output; pure refactor, keeps stochastic behavior bit-for-bit).

`sample_pitch(...)` is reused, with ONE additive optional parameter (default
None preserves stochastic behavior exactly):
```python
def sample_pitch(prev_pitch, note_pitch_list, note_probability_list, base_pitch,
                 interval_gravity, pitch_gravity, u, exclude_pitch=None):
    raw = (... existing computation ...)
    if exclude_pitch is not None:
        raw = raw.copy()
        raw[note_pitch_list == exclude_pitch] = 0.0
        if raw.sum() <= 0:          # degenerate: only the excluded pitch had weight
            return int(exclude_pitch)   # caller treats "unchanged" as no-op
    cum = cumsum(raw / raw.sum()); return int(note_pitch_list[searchsorted(cum, u)])
```
The MUST-CHANGE rule (case a) passes exclude_pitch = current pitch; everywhere
else passes None (same pitch allowed).

---

## 2. `src/config.py` — add elaboration loader + shared helpers

Keep the existing `load_config` (stochastic) untouched. Add a parallel loader
for elaboration with its own required-key set, reusing the shared scalar checks.

REQUIRED_KEYS_ELAB:
```
tempo, beats_per_bar, divisions_per_beat, bars_per_cycle,
base_pitch, note_probability, max_pitch_range,
interval_gravity, pitch_gravity,
division_change_probability, division_rest_probability,
division_extension_probability,
num_tracks, total_cycles, output_dir
```
Optional (with defaults):
```
changes_per_cycle = 1
reverse_cycle_order = True
seed = None
description (free text, ignored)
```
(start_cycle_on_base_pitch is NOT used by elaboration: cycle 0 is always the
single base_pitch note, so the first cycle inherently starts on base_pitch.)

NEW functions:
```python
def load_elaboration_config(path) -> dict
def _validate_elaboration(cfg, path) -> None
def normalize_division_vector(value, divisions_per_bar, divisions_per_cycle, name) -> list[float]
    # Accept: scalar -> [scalar]*divisions_per_cycle
    #         length == divisions_per_bar   -> tiled bars_per_cycle times
    #         length == divisions_per_cycle -> used as-is
    #         else -> ValueError
```

Validation specifics:
- Reuse stochastic checks for: tempo>0; positive ints (beats_per_bar,
  divisions_per_beat, bars_per_cycle, num_tracks, total_cycles); note_probability
  length 12, non-negative, sum>0; base_pitch in [0,127]; max_pitch_range non-neg
  int with band in [0,127]; output_dir non-empty str; seed null/non-neg int;
  description str.
- interval_gravity, pitch_gravity: numbers > 0.
- changes_per_cycle: positive int (default 1).
- reverse_cycle_order: bool (default True).
- The three division_* vectors are first run through normalize_division_vector
  (so the stored cfg value is always a full divisions_per_cycle-length list):
    - division_change_probability: entries >= 0, sum > 0 (it is a WEIGHT vector).
    - division_rest_probability: each entry in [0, 1] (DIRECT probabilities).
    - division_extension_probability: each entry in [0, 1] (DIRECT probabilities).
- Reject any stochastic-only keys if present? -> NO (ignore unknown keys; just
  warn-free). But explicitly do NOT require them.

---

## 3. `src/elaborator.py` — NEW (the algorithm)

### Representation
A cycle is a per-division grid of length `divisions_per_cycle`. Each division
carries a state and (for sounding divisions) a pitch:

```python
START, SUSTAIN, REST = 0, 1, 2          # division kinds

@dataclass
class Grid:
    kind:  list[int]    # len divisions_per_cycle, each in {START, SUSTAIN, REST}
    pitch: list[int|None]  # pitch at each division (set on START and its SUSTAINs;
                           # None on REST). Carrying pitch on SUSTAINs makes
                           # "nearest sounding pitch" O(1) and reversal trivial.
```
Invariant: a SUSTAIN at d means d-1 is START or SUSTAIN of the same note; its
pitch equals that note's pitch. A note = a maximal START followed by SUSTAINs.

### Core helpers
```python
def init_cycle(divisions_per_cycle, base_pitch) -> Grid
    # kind[0]=START, kind[1:]=SUSTAIN; pitch[:]=base_pitch  (one note, whole cycle)

def is_sounding(grid, d) -> bool          # 0<=d and kind[d] in {START, SUSTAIN}
def nearest_prev_pitch(grid, d, base_pitch) -> int
    # scan d-1, d-2, ... for first sounding division; return its pitch; else base_pitch
def note_end(grid, d) -> int
    # given START/SUSTAIN at d, return last division index of that note
def note_start_index(grid, d) -> int
    # given START/SUSTAIN at d, return the START index of that note
```

### Pitch sampling wrapper
```python
def sample_new_pitch(grid, d, cfg, pitch_lists, rng, exclude_pitch=None) -> int
    prev = nearest_prev_pitch(grid, d, cfg["base_pitch"])
    u = rng.random()
    return generator.sample_pitch(prev, *pitch_lists, cfg["base_pitch"],
                                  cfg["interval_gravity"], cfg["pitch_gravity"], u,
                                  exclude_pitch=exclude_pitch)
```
- case (a) resample passes exclude_pitch = grid.pitch[d] (MUST change).
- case (b) new note passes exclude_pitch = None (same pitch allowed).

### Forward-extension subroutine (shared)
```python
def forward_extension(grid, note_start_d, cfg, rng) -> None
    # The note currently ends at note_end(grid, note_start_d). Try to absorb the
    # next division j = end+1, end+2, ...:
    #   if j >= divisions_per_cycle: stop
    #   if kind[j] == START: stop (cannot overlap next note)
    #   if kind[j] == REST:
    #        if rng.random() < division_extension_probability[j]:
    #             kind[j] = SUSTAIN; pitch[j] = pitch[note_start_d]; continue
    #        else: stop
```

### The three case rules
```python
def apply_change(grid, d, cfg, pitch_lists, rng) -> None
    k = grid.kind[d]

    if k == START:                                  # case (a)
        if rng.random() < cfg["division_rest_probability"][d]:
            _make_rest_run(grid, d)                 # this note's [d..end] -> REST
            # (a note START becoming a rest: the WHOLE note becomes rest, since
            #  d is its start. Confirm: yes — start division selected, note->rest.)
        else:
            # MUST change: exclude the current pitch (no-op if exclusion degenerate)
            grid.pitch[d] = sample_new_pitch(grid, d, cfg, pitch_lists, rng,
                                             exclude_pitch=grid.pitch[d])
            # propagate new pitch to this note's SUSTAINs:
            for j in range(d+1, note_end(grid,d)+1): grid.pitch[j] = grid.pitch[d]

    elif k == REST:                                 # case (b)
        if is_sounding(grid, d-1):
            if rng.random() < cfg["division_extension_probability"][d]:
                # extend preceding note to cover d, then keep extending
                pd = note_start_index(grid, d-1)
                grid.kind[d] = SUSTAIN; grid.pitch[d] = grid.pitch[pd]
                forward_extension(grid, pd, cfg, rng)
                return
        # else / draw failed / d==0 -> start a NEW note at d
        grid.kind[d] = START
        grid.pitch[d] = sample_new_pitch(grid, d, cfg, pitch_lists, rng)
        forward_extension(grid, d, cfg, rng)

    else:                                           # case (c) SUSTAIN (mid-note)
        # split: first segment keeps original pitch; second segment [d..end]:
        if rng.random() < cfg["division_rest_probability"][d]:
            _make_rest_run(grid, d)                 # d..note_end -> REST
        else:
            grid.kind[d] = START                    # re-articulate same pitch
            # pitch[d..end] already equals the note pitch; nothing else to do
```
Helper:
```python
def _make_rest_run(grid, d) -> None
    end = note_end(grid, d)
    for j in range(d, end+1): grid.kind[j] = REST; grid.pitch[j] = None
```
OPEN CONFIRM (case a): when a NOTE START is chosen and converts to a rest, the
WHOLE note (start + its sustains) becomes a rest. (Alternative: only division d
becomes a rest and the sustains become a new... no — d is the start, so the note
is the unit. Assumed whole-note -> rest.)

### Picking where to act + cumulative cycles
```python
def pick_division(cfg, rng) -> int
    w = cfg["division_change_probability"]          # full-length weight vector
    cum = cumsum(w)/sum(w); return searchsorted(cum, rng.random())

def elaborate_once(grid, cfg, pitch_lists, rng) -> None   # in place, one edit
    d = pick_division(cfg, rng); apply_change(grid, d, cfg, pitch_lists, rng)

def generate_track(cfg, rng) -> list[Grid]
    dpc = divisions_per_cycle
    pitch_lists = generator.build_pitch_lists(base_pitch, max_pitch_range, note_probability)
    grid = init_cycle(dpc, base_pitch)
    cycles = [deepcopy(grid)]                        # cycle 0 = single note
    for _ in range(cfg["total_cycles"] - 1):
        for _ in range(cfg["changes_per_cycle"]):
            elaborate_once(grid, cfg, pitch_lists, rng)
        cycles.append(deepcopy(grid))               # cumulative snapshot
    if cfg["reverse_cycle_order"]:
        cycles.reverse()
    return cycles
```

### Grid -> StepEvent (for midi_writer)
```python
def grid_to_stepevents(grid, divisions_per_cycle) -> list[StepEvent]
    # walk divisions; emit a StepEvent per note (START..last SUSTAIN) and per
    # REST run (pitch=None) to preserve timing. Reuse generator.StepEvent.
```
midi_writer.track_to_note_events already drops pitch=None, so rest StepEvents are
harmless and keep the grid<->events mapping clean.

---

## 4. `scripts/generate_elaboration.py` — NEW (thin CLI)

Mirror generate_stochastic.py:
```
- sys.path bootstrap to ../src
- argparse: config_path, --seed (overrides cfg["seed"])
- cfg = config.load_elaboration_config(config_path)
- assert PPQN % divisions_per_beat == 0   (same guard as stochastic)
- rng = np.random.default_rng(seed)
- tracks = [elaborator.generate_track(cfg, rng) for _ in range(num_tracks)]
       -> each track is a list[Grid]
- convert: for each track, [grid_to_stepevents(g, dpc) for g in track]  (a Track)
- steps_per_cycle = dpc; ticks_per_step = PPQN // divisions_per_beat
- midi_tracks = [midi_writer.track_to_note_events(t, dpc, ticks_per_step) ...]
- track names: "<name_base> <i>"  (name_base = stem after last "_")
- midi_writer.write_midi(midi_tracks, tempo, output_path, names)
- print_summary: per-track note/rest counts + elapsed
```
Output filename: `<config_stem>_<YYYYMMDD>_<HHMMSS>.mid` in output_dir (same as
stochastic). No forward/reversed split — elaboration produces num_tracks tracks.

---

## 5. `config/elaboration/test.json` — NEW
Created now (see file). Modeled on gen_108_4_triplet_03125_004.json, minus
stochastic-only keys, plus the elaboration params.

---

## 6. Verification plan
1. `python scripts/generate_elaboration.py config/elaboration/test.json --seed 1`
   -> writes a .mid, prints summary, exits 0.
2. Determinism: same --seed => identical summary counts.
3. Sanity: with reverse_cycle_order=true, the LAST cycle of every track is the
   single sustained base_pitch note (one note, full cycle); the FIRST cycle is
   the most elaborated. (Spot-check via a small debug print or a tiny config
   with total_cycles=3, num_tracks=1.)
4. Re-run stochastic pathway to confirm the generator.py refactor didn't change
   its output (same seed => same counts as before refactor).

---

## 7. Resolved design decisions (Ken, 2026-06-13)
- case (a) NOTE-START -> rest converts the WHOLE note to a rest. CONFIRMED
  (rules as written; may revisit later — Ken curates output by ear).
- division_start_probability is GONE for the elaboration pathway (superseded by
  division_change_probability + division_rest_probability). CONFIRMED. May be
  reintroduced in some form later.
- start_cycle_on_base_pitch is DROPPED from elaboration: cycle 0 is always the
  single base_pitch note, so cycle 1 inherently starts on base_pitch. CONFIRMED.
- PITCH MUST-CHANGE rule: case (a) resample excludes the current pitch (new pitch
  must differ); case (b) new-note-after-rest may reuse the previous pitch.
  CONFIRMED.
