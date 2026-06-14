# music-automation

Randomly generates MIDI files for testing, driven by a JSON config (e.g. `config/stochastic/test.json`).

## Concepts

### Cycle and steps

Music is generated one **cycle** at a time. A cycle is divided into a fixed grid of **steps**:

```
steps_per_cycle = divisions_per_beat * beats_per_bar * bars_per_cycle
```

A single function generates one cycle's worth of notes for a track. The top-level loop calls this function repeatedly until `total_cycles` cycles have been produced.

### Steps per bar

A **bar** contains `divisions_per_beat * beats_per_bar` steps. This is the unit over which `division_start_probability` is defined (see below).

## Configuration

Config is read from a JSON file (e.g. `config/stochastic/test.json`). Fields:

| Field | Meaning |
|---|---|
| `tempo` | Tempo in beats per minute. |
| `beats_per_bar` | Time-signature numerator (beats in one bar). |
| `divisions_per_beat` | Subdivisions of a beat (e.g. `3` = triplets, `4` = sixteenths). One step = one subdivision. |
| `bars_per_cycle` | Number of bars in one generated cycle. |
| `base_pitch` | MIDI pitch that serves as the reference / center. |
| `note_probability` | Length-12 array of relative weights for the 12 pitch classes (semitones above `base_pitch`, mod 12). Index 0 is the root. |
| `division_start_probability` | Length-(`divisions_per_beat * beats_per_bar`) array of relative weights for which step within a bar a note may start on. |
| `max_pitch_range` | Half-width (in semitones) of the allowed pitch range around `base_pitch`. The allowed pitches are `base_pitch - max_pitch_range` through `base_pitch + max_pitch_range`, inclusive — a total of `2 * max_pitch_range + 1` pitches. E.g. `base_pitch = 60`, `max_pitch_range = 24` gives pitches 36–84. |
| `interval_gravity` | Gaussian width (semitones, `> 0`) of the penalty on the interval from the previous note when choosing a pitch (Step 2). Smaller = smoother, more stepwise motion; larger = bigger leaps allowed. Factor: `exp(-(pitch - prev_pitch)² / interval_gravity²)`. |
| `pitch_gravity` | Gaussian width (semitones, `> 0`) of the pull toward `base_pitch` (registral center). Used both when choosing a pitch (Step 2) and when deciding early termination (Step 5). Smaller = stays near center; larger = wider register. Factor: `exp(-(pitch - base_pitch)² / pitch_gravity²)`. |
| `step_length_scale` | Gaussian width (steps, `> 0`) controlling how far ahead the next note may start, i.e. the note-length scale (Step 3). Smaller = shorter notes / next start nearby; larger = longer notes / can jump further ahead. Factor: `exp(-(step - current_step)² / step_length_scale²)`. |
| `ending_gravity` | Gaussian width (steps, `> 0`) controlling early cycle termination vs. distance from the cycle end (Step 5). Larger = cycles more likely to terminate sooner. Factor: `exp(-(steps_per_cycle - current_step)² / ending_gravity²)`. |
| `rest_probability` | Ceiling in `[0, 1]` on the per-note chance of being converted to a rest (Step 4). Actual chance is `rest_probability * (1 - attractiveness / max_attractiveness)`, so less-attractive notes (low pitch-class and/or beat weight) are more likely to become rests. A rest keeps its duration but emits no MIDI. |
| `num_tracks` | Number of parallel MIDI tracks to generate. |
| `total_cycles` | Number of cycles to generate in the output file. |
| `output_dir` | Directory to write the resulting `.mid` file into. |
| `track_direction` | Optional, `"forward"`, `"reversed"`, or `"both"` (default `"both"`). Selects which tracks are written: only the forward tracks, only their time-reversed companions, or both. Reversed tracks are always derived from the forward tracks (per-cycle reversal — see "Time-reversed tracks" below). |
| `random_number_change_probability` | Optional, default `1.0`. Controls how much the per-cycle random-number table changes from one cycle to the next within a track. At `1.0`, every cycle uses a completely fresh table (independent cycles). At `0.0`, the entire track reuses one fixed table (every cycle is identical). Intermediate values cause that fraction of cells in the table to be re-drawn between cycles, giving gradual cycle-to-cycle drift. Each track always starts with a fully fresh table. See "Cycle-to-cycle randomness drift" below. |
| `start_cycle_on_base_pitch` | Optional, default `false`. If `true`, the first note of every cycle is set to `base_pitch` exactly, bypassing the pitch-sampling formula for that note. Useful for periodic, "returns-home" structures where each cycle should begin on the tonic. |
| `description` | Optional free-text note describing the config. Ignored by the generator — purely for human reference. |
| `seed` | Optional RNG seed (non-negative int) for reproducible output. Default `null` = a fresh random seed each run. The `--seed` command-line argument overrides this value. |
| `reversal_last_note_start_step` | Optional integer ≤ 0, default `0`. Shifts each reversed cycle so the last reversed event starts `-N` steps before the cycle end, then clips to the cycle. `0` is off (pure mirror). See "Time-reversed tracks" below. |

## Cycle generation

Generation of one cycle proceeds in steps. The first step is to expand the user-supplied weight arrays so that each grid position has its own weight.

### Step 1 — Expand weight arrays

**Per-step start weights.** `division_start_probability` covers a single bar (length `divisions_per_beat * beats_per_bar`). It is **tiled** `bars_per_cycle` times to produce `division_start_list`, a per-step weight vector of length `steps_per_cycle`:

```
division_start_list[s] = division_start_probability[s mod (divisions_per_beat * beats_per_bar)]
```

**Per-pitch weights.** `note_probability` covers one octave (length 12), with index `0` aligned to `base_pitch`. It is **tiled across the full pitch range** so that MIDI pitch `p` gets weight `note_probability[(p - base_pitch) mod 12]`. Two parallel length-`(2 * max_pitch_range + 1)` arrays result:

- `note_pitch_list = [base_pitch - max_pitch_range, …, base_pitch + max_pitch_range]` — the candidate MIDI pitches.
- `note_probability_list[i] = note_probability[(note_pitch_list[i] - base_pitch) mod 12]` — the per-pitch base weight.

For the example config (`base_pitch=60`, `max_pitch_range=24`), `note_pitch_list` runs 36…84 (49 entries).

(Mod is the mathematical modulo: for `p < base_pitch`, `(p - base_pitch) mod 12` still lands in `0…11`.)

### Step 2 — Choose the next note's pitch

For each step in the cycle for which a note is generated, the pitch of the new note is drawn from a distribution that combines three factors:

1. **Pitch-class weight** — the base preference from `note_probability_list`.
2. **Interval gravity** — a Gaussian penalty on the size of the interval (in semitones) from the previously chosen note. Width is set by `interval_gravity`. Larger jumps are exponentially less likely.
3. **Pitch gravity** — a Gaussian penalty on the absolute distance (in semitones) from `base_pitch`. Width is set by `pitch_gravity`. Pitches far from the center are exponentially less likely.

Let `prev_pitch` be the pitch of the previous note. For the first note of **every cycle**, `prev_pitch = base_pitch` (treated as if an imaginary note at `base_pitch` had just been played — each cycle starts melodically "fresh," with no carry-over from the previous cycle). For every subsequent note within a cycle, `prev_pitch` is the pitch actually chosen for the most recent note.

If the optional flag `start_cycle_on_base_pitch` is `true`, the formula below is bypassed for the first note of each cycle and the pitch is set to `base_pitch` directly (the corresponding random draw for that note is unused).

The raw weight for each candidate pitch is then computed elementwise across `note_pitch_list`:

```
note_transition_prob_raw = note_probability_list
                         * exp( -(note_pitch_list - prev_pitch)**2 / interval_gravity**2 )
                         * exp( -(note_pitch_list - base_pitch)**2 / pitch_gravity**2 )
```

Normalize and take the cumulative sum:

```
note_transition_prob      = note_transition_prob_raw / sum(note_transition_prob_raw)
note_transition_prob_cum  = cumulative_sum(note_transition_prob)        # last entry == 1.0
```

Draw `u` uniformly from `[0, 1)`, and choose the pitch at the smallest index `i` for which `note_transition_prob_cum[i] >= u`. (This is standard inverse-CDF sampling; in NumPy it's `np.searchsorted(note_transition_prob_cum, u)`.) The chosen `note_pitch_list[i]` becomes the new note's pitch, and is also the `prev_pitch` for the next pitch draw.

### Step 3 — Choose when the next note starts (sets the current note's duration)

A note's duration is determined indirectly: we pick the step on which the **next** note will begin, and the current note's duration is the number of steps between them.

Let `current_step` be the step on which the current note begins (an integer in `0 … steps_per_cycle - 1`). The very first note of each cycle is generated with `current_step = 0`.

For sampling, we use an extended candidate index that has one extra slot beyond the cycle:

```
step_number = [0, 1, …, steps_per_cycle]        # length steps_per_cycle + 1
```

The virtual final index `steps_per_cycle` represents the **end-of-cycle** option: choosing it means the current note runs all the way through the cycle's final step (`steps_per_cycle - 1`) and no further note is generated this cycle.

**Build the candidate weight vector.**

1. Start with a length-`(steps_per_cycle + 1)` copy of `division_start_list`, with `division_start_list[0]` repeated in the extra slot (this is the natural periodic extension — slot `steps_per_cycle` is conceptually "step 0 of the next cycle"):

   ```
   division_start_list_ext = concatenate(division_start_list, [division_start_list[0]])
   ```

2. Mask invalid candidates. The next note must begin strictly after `current_step`, so zero out indices `0 … current_step` inclusive. Remember the total raw weight removed:

   ```
   eliminated = sum(division_start_list_ext[0 : current_step + 1])
   division_start_list_ext[0 : current_step + 1] = 0
   ```

3. Recycle the eliminated mass into the end-of-cycle slot. (`division_start_list[0]` is always masked because `current_step ≥ 0`, so the periodic-extension value never functions as a future candidate; the slot is "free" to repurpose for the eliminated mass.)

   ```
   division_start_list_ext[steps_per_cycle] = eliminated
   ```

**Weight by step-length scale.** A Gaussian falloff (width `step_length_scale`) makes near-future starts more likely than far-future ones, and — because the end-of-cycle slot sits at distance `steps_per_cycle - current_step` from the current step — naturally suppresses end-of-cycle when `current_step` is far from the boundary, while letting it dominate as `current_step` approaches the boundary:

```
next_note_start_prob_raw = division_start_list_ext
                         * exp( -(step_number - current_step)**2 / step_length_scale**2 )
```

Normalize and take the cumulative sum:

```
next_note_start_prob      = next_note_start_prob_raw / sum(next_note_start_prob_raw)
next_note_start_prob_cum  = cumulative_sum(next_note_start_prob)
```

Draw `u` uniformly from `[0, 1)`, and pick the smallest index `i` for which `next_note_start_prob_cum[i] >= u` (`np.searchsorted(next_note_start_prob_cum, u)`). That index is `next_note_start`.

The current note's duration is:

```
duration = next_note_start - current_step       # in steps
```

**Iteration and termination.**

- If `next_note_start < steps_per_cycle`, it becomes the `current_step` of the following note, and Steps 2 and 3 repeat.
- If `next_note_start == steps_per_cycle`, the current cycle's note generation is complete. (Duration is `steps_per_cycle - current_step` — the current note fills the rest of the cycle.) The next cycle then begins with a fresh `current_step = 0`.

### Step 4 — Convert notes to rests (post-processing sweep)

After all cycles have been generated and each note is known as a triple `(pitch, start_step, duration)`, iterate over every note and decide independently whether to convert it to a rest. A note is more likely to become a rest when its pitch class has low weight, when its start step within the bar has low weight, or both.

For a note with pitch `p` starting at step index `s` (within its cycle, `0 ≤ s < steps_per_cycle`):

```
pitch_weight = note_probability[(p - base_pitch) mod 12]
step_weight  = division_start_probability[s mod (divisions_per_beat * beats_per_bar)]

attractiveness     = pitch_weight * step_weight
max_attractiveness = max(note_probability) * max(division_start_probability)
p_rest             = rest_probability * (1 - attractiveness / max_attractiveness)
```

Draw `u ~ Uniform[0, 1)`. If `u < p_rest`, convert the note to a rest. **Converting to a rest preserves the note's timing structure** — its `duration` remains the same so the rhythmic grid is unchanged — but no MIDI note-on/note-off is emitted for that span.

**Interpretation of `rest_probability`.** It is the *ceiling* on per-note rest probability:

- A note at the maximum joint weight (the most-favored pitch class on the most-favored step within the bar) has `p_rest = 0` and is never converted.
- A note whose pitch-class weight or start-step weight is `0` has `p_rest = rest_probability`.
- Notes in between scale linearly in `1 - attractiveness / max_attractiveness`.

Because `attractiveness` is the product of the two weights, either a weak pitch on a strong beat or a strong pitch on a weak beat produces a substantial `p_rest` — both axes pull the note toward becoming a rest.

### Step 5 — Early cycle termination

Within each cycle, the note-generation loop normally continues until the chosen `next_note_start` reaches `steps_per_cycle` (the natural cycle boundary; see Step 3). Early termination provides an additional way for a cycle to end on a musically natural note — one with high pitch-class weight, on a high-weight beat, near the end of the cycle, and close to `base_pitch`.

`p_terminate` is evaluated **at each note's start during generation** (not as a post-processing sweep). If it fires, that note is the last note generated **for the current cycle**: it plays normally with its computed duration, the remainder of *that cycle* becomes silence, and the loop then advances to the **next** cycle (which begins normally with a fresh `current_step = 0` and `prev_pitch = base_pitch`). Generation always continues for the full `total_cycles` cycles; Step 5 only shortens individual cycles, it never ends the piece.

For a note with pitch `p` starting at step `current_step` within its cycle:

```
pitch_weight = note_probability[(p - base_pitch) mod 12]
step_weight  = division_start_probability[current_step mod (divisions_per_beat * beats_per_bar)]

p_terminate = (pitch_weight * step_weight / max_attractiveness)
            * exp( -(steps_per_cycle - current_step)**2 / ending_gravity**2 )
            * exp( -(p - base_pitch)**2 / pitch_gravity**2 )
```

Draw `u ~ Uniform[0, 1)`. If `u < p_terminate`, generation terminates after this note.

Each factor is in `[0, 1]`, so `p_terminate ∈ [0, 1]` is always a valid probability:

| Factor | Peak value (= 1) when … | Suppresses termination when … |
|---|---|---|
| `pitch_weight * step_weight / max_attractiveness` | both the pitch class and the start step are at maximum weight | either weight is small (low-attractiveness note) |
| `exp( -(steps_per_cycle - current_step)**2 / ending_gravity**2 )` | the note begins at the cycle boundary | the note begins mid-cycle (width `ending_gravity`) |
| `exp( -(p - base_pitch)**2 / pitch_gravity**2 )` | the note's pitch equals `base_pitch` | the note is far from `base_pitch` (width `pitch_gravity`) |

Note that `pitch_gravity` is reused from Step 2 — it sets the same width of "tolerance around the tonic." Termination is therefore most likely on a strong beat, near the end of a cycle, on a pitch close to the tonic — a musically sensible ending.

### Cycle-to-cycle randomness drift

The cycle generator consumes one block of uniform random numbers per cycle: a 2-D array with one row per random source (pitch selection, next-note start, termination decision, rest-sweep decision) and one column per step within the cycle.

Within a single track, this random-number table evolves across cycles according to `random_number_change_probability` (`p`):

- **Cycle 0** of every track: the table is drawn entirely fresh from the RNG.
- **Cycle k → cycle k + 1**: each cell of the table is, independently, **re-drawn with probability `p`** and **kept unchanged with probability `1 - p`**.

Effect across the spectrum of `p`:

| `p` | Behavior |
|---|---|
| `1.0` | Every cycle uses a completely independent table — full random variation, identical to a no-memory setting. |
| `0.5` | About half the cells turn over each cycle; substantial change but recognizable continuity. |
| `0.05` | About 5% of cells change each cycle; the table drifts slowly so consecutive cycles sound like variations on each other. |
| `0.0` | The table never changes after cycle 0. Combined with the per-cycle reset of `current_step = 0` and `prev_pitch = base_pitch`, every cycle in the track is bit-for-bit identical. |

Each new track gets its own fresh starting table, so tracks remain melodically independent regardless of `p`.

Subsequent steps (velocity / volume, multi-track behavior, etc.) will be defined in following sections.

## Output

The generator produces a **single MIDI file** per run, written to `output_dir`. Multiple tracks all live inside that one file (not split into separate files). This matches the convention used by the code preserved in `./archive`.

**File name.** The output file is named after the input config's basename, with a timestamp appended:

```
<config_basename>_<YYYYMMDD>_<HHMMSS>.mid
```

For example, running with `config/stochastic/test.json` produces something like `test_20260511_142307.mid` inside `output_dir`. The timestamp ensures successive runs don't overwrite each other.

The file is a standard Type 1 (multi-track) MIDI file with the following structure:

- **Track 0** carries the tempo meta-message (`set_tempo` derived from `tempo`). It contains no note data.
- **Tracks 1 … `num_tracks`** each contain the notes generated for one of the requested tracks.

When `num_tracks > 1`, each track is generated by running the cycle-generation procedure (Steps 1–5) independently — independent random draws, but using the same shared configuration values. The tracks are therefore melodically independent voices drawn from the same distribution.

**MIDI channel.** Every note on every track is emitted on **MIDI channel 0** (single shared channel). Per-track channel/instrument assignment can be added later.

**Velocity.** For now, every note is emitted with MIDI velocity `100`. A velocity model can be added later.

### Time-reversed tracks

When `track_direction` includes reversed tracks (`"reversed"` or `"both"`), each forward track has a **time-reversed companion track** built from the same generated events — no new random draws.

The reversal is performed **per cycle**, independently:

1. For each cycle, take that cycle's ordered event list `[(pitch_or_REST, duration), …]`.
2. Reverse the list.
3. Recompute start steps as the cumulative sum of durations in the reversed order, starting at step `0` of that cycle.

What was the last event of forward cycle *k* therefore starts at step `0` of reversed cycle *k*; what was the first event ends at the end of the cycle. Rests stay rests; durations are preserved; cycle boundaries are preserved (each reversed cycle is the same length as its forward partner).

In the rare case that the cycle containing the Step 5 termination event has trailing silence (post-termination), that silence is implicit (not an event) and remains as trailing silence in the reversed cycle.

**Shifting the last reversed note: `reversal_last_note_start_step`** (optional, integer ≤ 0, default `0`).

By default the reversed cycle ends with what was the first forward event landing on the final step of the cycle. Set this key to a negative value `N` to instead shift the entire reversed cycle so the last event *starts* at step `steps_per_cycle + N`. Example: with `divisions_per_beat = 4`, `beats_per_bar = 4`, `bars_per_cycle = 4` (`steps_per_cycle = 64`) and `reversal_last_note_start_step = -4`, the last reversed event of each cycle starts on the downbeat of the cycle's last beat (step 60).

After the shift, every event is clipped to the in-cycle range `[0, steps_per_cycle)`:

- If the shifted last event would extend past the cycle end, its duration is truncated so it ends at the cycle boundary (becoming `-N` steps long).
- If an earlier event would start before step 0, its start is clamped to 0 and its duration reduced; events whose duration would become ≤ 0 are dropped.

The value `0` is a sentinel meaning "off" (the pure mirror is returned). The key has no effect when `track_direction == "forward"`.

**MIDI file layout when `track_direction` is `"both"`** (for `num_tracks = N`):

- Track 0 — tempo
- Tracks 1 … N — forward tracks
- Tracks N+1 … 2N — reversed tracks (track *N+i* is the reversed companion of track *i*)

With `"forward"` only tracks 1 … N are written; with `"reversed"` only the N reversed tracks are written.

All notes remain on MIDI channel 0.

## Status

Implemented. The generation logic described above lives in `generator.py`
(pure cycle/track generation and per-cycle reversal), with `config.py` loading
and validating the JSON config, `midi_writer.py` emitting the MIDI file, and
`scripts/generate_stochastic.py` as the CLI entry point
(`python scripts/generate_stochastic.py config/stochastic/<name>.json [--seed N]`).
Shared modules live in `src/`; configs for this pathway live in `config/stochastic/`.
