# Learned workflow: specification

Status: DRAFT, 2026-11-22. Both stages are implemented: analysis in `scripts/analyze_midi.py`
(`src/learner.py`) and generation in `scripts/generate_learned.py`
(`src/learned_generator.py`, `src/dawproject_writer.py`). See section 9.
Questions raised while writing it, and their answers, are in section 11 (`Q:` / `A:` form).

A third generation workflow, alongside **stochastic** and **elaboration**. Instead of
drawing from hand-set weights, it **learns probability tables from a MIDI melody** and
uses them to write new melodies. Each 4-bar cycle has a fixed rhythmic form (`AB AB AA BC`)
and ends on the tonic. The melody is filled in **backwards** from that final tonic.

The procedure is stated in general terms. One concrete case runs through it as a worked
example: 4/4, 16th-note grid, 4-bar cycles, D dorian, 84 bpm (the N59 piece).

---

## 1. Overview

The workflow is two separate programs joined by a **tables file**:

```
  input .mid ──► [analyze] ──► tables .json ──► [generate] ──► output .dawproject (+ .mid)
                                    ▲                ▲
                         edit by hand here    generation config
                         (or write from        (form, N, M, seeds,
                          scratch)              tempo, thresholds)
```

- **Analyze** reads the MIDI and writes the tables. It knows nothing about forms, seeds
  or output.
- **Generate** reads only the tables file and its own config. It never sees the MIDI.
  Tables can therefore be tuned by hand between the two steps, or written without any MIDI.

### Parameters, with the worked-example values

| Parameter | Example | Used by | Meaning |
|---|---|---|---|
| `root` | `"D"` | analyze | Tonic pitch class. |
| `mode` / `scale_semitones` | `"dorian"` = `[0,2,3,5,7,9,10]` | analyze | The key's scale above the root. A mode name or an explicit list. |
| `chord` | `"Dm7"` | analyze | The chord the whole input sits on. Recorded only, for now (Q4). |
| `base_pitch` | `62` (D4) | both | MIDI pitch of the root that counts as scale step 0. The generated cycle ends here. |
| `divisions_per_beat` | `4` (16ths) | both | Grid resolution. |
| `input_quantization` | `"1/16"` | analyze | Grid the input was quantized to. Notes are moved to its nearest line before analysis (section 2). |
| `beats_per_bar` | `4` | both | Time-signature numerator. |
| `bars_per_cycle` | `4` | both | Bars in one generated cycle (= one input segment). |
| `period` | `16` (one bar) | analyze | Length, in grid steps, of the position tables. `16` pools all bars; `64` keeps the four bars of a cycle apart but needs four times the data. |
| `snap_priority` | `[0,2,4,5]` (root, 3rd, 5th, 6th) | analyze | Scale-degree order used to snap chromatic notes. |
| `skip_silent_bars` | `2` | analyze | Runs of at least this many bars with no note sounding are left out of T1. `0` keeps every bar. |
| `form` | `"AB AB AA BC"` | generate | Cycle layout in half-bars (section 5). |
| `num_cycles` (N) | e.g. `64` | generate | Cycles per track. |
| `num_tracks` (M) | e.g. `8` | generate | Parallel tracks. |
| `seed` | `59` | generate | Master seed (section 7). |
| `max_context` | `3` | generate | Longest transition context used. |
| `min_context_count` | `3` | generate | A context is used only if its total weight is at least this. |
| `min_position_count` | `3` | generate | A position's pitch table is used only if its total weight is at least this. |
| `tempo` | `84` | generate | Written into the `.dawproject`. |

---

## 2. Input and preprocessing (analyze)

1. **Input:** one MIDI file holding a single melody, all on one chord, with bar 1
   starting at time 0. Cubase-quantized files are close to the grid but not exactly on
   it (see "Test file" below), so the input is cleaned first.
2. **Cleaning.** These steps run in this order, and each is counted in the analysis
   report:
   1. **Quantize:** move every note start and end to the nearest line of the stated
      quantization grid (`input_quantization`, 16ths here).
   2. **Ignore zero-length notes:** drop every note whose quantized length is zero.
      This happens before steps 3 and 4, so a note that has vanished cannot remove or
      cut short another note.
   3. **Same-start notes:** when two or more notes start on the same line, keep only the
      **longest** (by quantized length) and delete the others. If two are equally long,
      keep the higher one.
   4. **Resolve overlaps:** sort notes by quantized start. If a note is still sounding
      when the next one starts, the earlier note **ends at the later note's quantized
      start**.
3. **Note events:** each note becomes `(start, length, midi_pitch)`, with start and length
   in grid steps (16ths). Silence between notes is a rest.
4. **Pitch to scale step.** Let `d = len(scale)` (7 here). Each pitch is written as a
   **scale step** relative to the root at `base_pitch`: step `0` is the root at
   `base_pitch`, and each step up is the next scale note, with no octave wrap.
   In D dorian, with `base_pitch = 62`:

   | Note | C4 | D4 | E4 | F4 | G4 | A4 | B4 | C5 | D5 | E5 |
   |---|---|---|---|---|---|---|---|---|---|---|
   | MIDI | 60 | 62 | 64 | 65 | 67 | 69 | 71 | 72 | 74 | 76 |
   | step | −1 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |

   Converting back: `midi = base_pitch + 12·floor(step/d) + scale_semitones[step mod d]`.
   The **degree class** of a step is `step mod d` (0…6), which is octave-equivalent:
   steps −1, 6 and 13 all have class 6.
5. **Snapping chromatic notes.** A note outside the scale lies between two neighbouring
   scale notes, and it is moved to one of them. The neighbour whose degree class comes
   first in `snap_priority` (root, 3rd, 5th, 6th) wins. If neither neighbour is in the
   list, the lower one wins. In D dorian:

   | Input | Neighbours | Snaps to | Why |
   |---|---|---|---|
   | C# | C (7th), D (root) | D | root |
   | D#/E♭ | D (root), E (2nd) | D | root |
   | F# | F (3rd), G (4th) | F | 3rd |
   | G#/A♭ | G (4th), A (5th) | A | 5th |
   | A#/B♭ | A (5th), B (6th) | A | 5th before 6th |

   Snapping is done once, here, so the tables and all output are strictly in the key.
6. **Segments.** The melody is cut into 4-bar segments (`bars_per_cycle`) from bar 1.
   Because the whole input is one chord, the **note sequence is treated as continuous
   across segment and bar lines** for the transition tables. A trailing partial segment
   is kept.

### Test file: `data/input/N56_2026-11-05q.mid` (checked 2026-11-22)

This replaces an earlier file, `N56_2026-11-14-05_quant.mid`, which had the melody
doubled an octave down.

**Current input: `data/input/transposed.mid`.** This is the same melody, with the
same timing. Ken transposed it by hand, section by section, so that it all sits on
**A aeolian / Am7** (the original changes chord). Its analysis (`config/learned/N59_analyze_transposed_Aaeolian.json`
→ `data/tables/N59_transposed_Aaeolian.json`) gives:

- **Cleaning and rhythm:** unchanged from the figures below (1,809 notes kept; same
  `p_start`).
- **Range:** MIDI 67–93 (G4–A6), median A5 (81). **`base_pitch` = 81 (A5)**, the
  main A, with 695 notes. A4 has only 79.
- **Notes:** still white notes only. By degree: root (A) 775, 5th (E) 482, 3rd (C) 209,
  2nd (B) 161, 4th (D) 114, 7th (G) 44, 6th (F) 24. Am7 chord tones are **83%** of
  notes. Before transposing, the best single chord (Am7) covered 67%.
- **Transitions at threshold 3:** k=1: 14 of 16 contexts, covering 100% of note instances;
  k=2: 55 of 89, covering 97%; k=3: 96 of 243, covering 89%.

The rest of this subsection describes the untransposed N56 file.

- Format: type 0, one track ("Grandeur"), 480 ticks per quarter, 108 bpm, 4/4.
  304 bars (76 four-bar segments), 1,825 notes, MIDI 67–88 (G4–E6), median E5 (76).
- **Key (from Ken): E phrygian**, `scale_semitones = [0,1,3,5,7,8,10]`
  (E F G A B C D), **chord Em7**. **`base_pitch` = 76 (E5)**: the lowest E in the file
  is E5, which has 532 notes; E6 has only 10.
  Analysis config:
  `{"root": "E", "mode": "phrygian", "chord": "Em7", "base_pitch": 76, "input_quantization": "1/16", ...}`.
- **Not exactly quantized.**
  - Every note **end** is on the 16th grid.
  - **Starts** are up to 7 ticks early or 4 ticks late (out of 120 per 16th).
  - There are 6 notes that quantize to zero length.
  - There are 10 same-start pairs, all B+E fourths or E+B fifths. They come in matching
    pairs 144 bars apart (bars 22/166, 69/213, 70/214, 101/245, 136/280).
- **After cleaning:** 6 zero-length notes are dropped, and each same-start pair keeps
  only its longer note. That leaves 1,809 notes, and no other note needs cutting.
- **Partly repeated, on purpose:** about 70% of the notes in bars 1–144 recur exactly
  144 bars later. **This is intended** (Ken, 2026-11-22): a repeated passage was one
  he liked, so it should count twice and raise the probability of those outcomes. The
  input is **not** de-duplicated.
- **Pitch content:** white notes only, all in E phrygian, so nothing needs snapping.
  By degree: root (E) 540, 4th (A) 462, 7th (D) 284, 5th (B) 217, 6th (C) 142,
  2nd (F) 99, 3rd (G) 65. The 3rd is the rarest note.
- **How full the tables would be,** with the threshold of 3:

  | Table | Coverage |
  |---|---|
  | T4, k=1 | all 13 contexts qualify |
  | T4, k=2 | 57 of 73 contexts qualify, covering 99% of note instances |
  | T4, k=3 | 115 of 221 contexts qualify, covering 89% of note instances |
  | T1–T3, `period` 16 | 3 to 298 starts per position (median 111) |
  | T1–T3, `period` 64 | 0 to 76 starts per position (median 4) |

  The data supports `max_context = 3` well. `period = 64` would be too thin.
- **`p_start` at `period` 16:**
  `.99 .71 .87 .10 | .97 .30 .97 .24 | .99 .33 .97 .13 | .97 .59 .79 .03`.
  This shows a strong 8th-note pulse. Position 1 is 0.71 only because the line is
  rarely free there: when it is free, a note usually starts. Starts per position:
  `298 10 145 8 | 287 6 272 4 | 148 4 219 7 | 277 10 111 3`. Every position has at
  least 3, so no position pitch table is switched off. On the odd 16ths, though, they
  rest on only 3–10 notes.
- **Figures above** are from `scripts/analyze_midi.py`
  (`config/learned/N59_analyze_N56_Ephrygian.json` → `data/tables/N59_N56_Ephrygian.json`).

---

## 3. Stage 1: the tables (analyze)

**Position** of a grid step is `s = step mod period`, from `0` to `period − 1`.
With `period = 16`, every bar contributes to the same 16 positions.

### T1 — onset probability `p_start[s]`

For each position s:

- `n_free[s]`: how many times position s was **not** covered by a note held over from an
  earlier position. That is, either a note started there or there was silence.
- `n_start[s]`: how many times a note started at s.
- `p_start[s] = n_start[s] / n_free[s]`, or `0` if `n_free[s] = 0`.

`p_start[s]` is the chance that a note starts at s **given the line is free there**. This
is the probability the rhythm walk (section 4) needs. Positions where notes rarely start
get a low value automatically.

**Long silences are left out** (Ken, 2026-11-23). A bar is silent if no note sounds in
it at all; a note held over from the previous bar counts as sounding. A run of at least
`skip_silent_bars` (default 2) silent bars is left out of `n_free`, so it does not lower
`p_start` everywhere. A single silent bar is kept, as a rest within the music. Only T1 is
affected: the other tables count notes, and rests are already ignored by the
transitions, so the note before a long silence still links to the note after it. The
`cleaning` line of the tables file records `silent_bars_skipped`. In the N45 melody,
23 bars were skipped (runs of 2, 2, 2, 2, 2, 3, 4 and 6 bars) and 20 single silent bars
were kept; the N59 and N45 bass inputs have no such runs.

### T2 — length distribution `lengths[s]`

For each position s, a count of the lengths (in grid steps) of the notes that start at s.
Lengths are recorded as they are in the input, even if a note crosses a half-bar or bar
line. Generation cuts notes where needed.

### T3 — positional pitch table `position_pitch[s]`

For each position s, a count of the **degree classes** (octave-equivalent, 0…6) of the
notes that start at s.

### T4 — backward transition tables `transitions[k]`, k = 1…`max_context`

Take the sequence of sounding notes in time order, as scale steps (rests are skipped, so
each note links to the next sounding note): `x[0], x[1], …`.

For order k, the **context** of note `x[i]` is the k notes that **follow** it,
`(x[i+1], …, x[i+k])`. The table counts which pitch `x[i]` appeared **before** each
context. Pitches are actual scale steps, so there is **no octave equivalence** here.

The table runs backwards because generation fills in a cycle from its end (section 6).

### The tables file (the contract between the two programs)

One JSON file, laid out for hand editing. It stores **weights (counts)**, not normalized
probabilities. Generate normalizes whatever it finds, so a hand edit is just a change of
weight:

- weights may be any non-negative number, not just integers;
- setting a weight to `0` forbids that choice;
- the thresholds (`min_context_count`, `min_position_count`) compare against the
  **total weight** of a context or position. Raising a total to the threshold turns it
  on; lowering it below turns it off.

`p_start` is the one exception. It is stored as a probability (0…1) and generate reads it
directly. `n_free` and `n_start` are stored beside it for reference only, so editing them
changes nothing.

Layout (keys are strings because JSON requires them; one entry per line):

```json
{
  "format": "music-automation learned tables v1",
  "source_midi": "data/input/N59_melody.mid",
  "root": "D",
  "mode": "dorian",
  "scale_semitones": [0, 2, 3, 5, 7, 9, 10],
  "chord": "Dm7",
  "base_pitch": 62,
  "divisions_per_beat": 4,
  "beats_per_bar": 4,
  "bars_per_cycle": 4,
  "period": 16,

  "onsets": {
    "p_start": [1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0.5, 0, 1, 0],
    "n_start": [2, 0, 0, 0, 2, 0, 1, 0, 2, 0, 1, 0, 1, 0, 1, 0],
    "n_free":  [2, 0, 0, 0, 2, 0, 1, 0, 2, 0, 1, 0, 2, 1, 1, 0]
  },

  "lengths": {
    "0":  {"4": 2},
    "4":  {"2": 1, "4": 1}
  },

  "position_pitch": {
    "0":  {"4": 2},
    "4":  {"2": 1, "3": 1}
  },

  "transitions": {
    "1": {
      "0":     {"1": 2},
      "2":     {"0": 1, "3": 1, "4": 1}
    },
    "2": {
      "1 0":   {"2": 2}
    },
    "3": {
      "2 1 0": {"3": 1, "4": 1}
    }
  }
}
```

**Reading a context key:** `"1 0"` means the following note is step 1 and the note after
that is step 0. Keys are written in time order, so they read like the melody. The inner
object gives the weight of each possible **preceding** step: `{"2": 2}` means step 2 came
before `1 0` twice.

On load, generate **validates** the file. It checks that the format string and grid match
its config, that every `p_start` is in 0…1, that every weight is non-negative, and that
every key parses. It also **reports** what it actually used: which contexts met the
threshold, which positions' pitch tables were active, and how often each fallback fired.

---

## 4. Stage 2a: the rhythm pattern (generate, once per track)

Each track m gets **one** one-bar rhythm pattern, drawn with the track's rhythm random
stream (section 7). It is used for all N cycles of that track.

**Walk through the bar:**

```
s = 0
while s < steps_per_bar:                      # 16
    if random() < p_start[s]:                 # a note starts here
        L = sample from lengths[s]
        L = min(L, half_bar_end(s) - s)       # cut at the half-bar line
        place note (s, L)
        s += L
    else:                                     # rest for one 16th
        s += 1
```

`half_bar_end(s)` is 8 for `s < 8` and 16 otherwise. Cutting at the half-bar keeps each
half self-contained, so the halves can be rearranged.

**Halves.** The bar splits into **A** (beats 1–2, steps 0–7) and **B** (beats 3–4,
steps 8–15).

**Ending half C.** A quarter note on the first beat of the half and a rest on the second.
The quarter note is the **tonic at `base_pitch`** (step 0).

---

## 5. The form

`form` lists the bars of one cycle, each written as two half-bar letters:

```
bar:     1     2     3     4
form:    AB    AB    AA    BC
```

Each letter's notes are placed at their offset within the half they land in. In bar 4,
B's notes move from beats 3–4 to beats 1–2, and C sits on beats 3–4. A cycle is
`bars_per_cycle` bars (64 sixteenths).

Generalizing: any string of A, B and C works, with one pair per bar; C must come last.
More letters (e.g. a second bar pattern giving D and E) can be added later.

**`form: "walk"`** (Ken, 2026-11-23, first used for N45). There are no halves and no C.
The rhythm walk of section 4 runs over the **whole cycle** (the tables'
`bars_per_cycle` bars) instead of one bar, reading `p_start` and the lengths at
position `s mod period`. A note is cut only at the end of the cycle (counted as
`notes_cut_at_cycle_end`). The resulting pattern is still fixed per track and repeated
every cycle. Its **last note is the end note**: with `end_note: "sampled"` it is drawn
from the tables at its own position, then the backward fill runs from it as usual. This
is meant for tables whose `period` is the whole cycle (e.g. 32 for two bars of 16ths),
so each bar of the cycle has its own onsets, lengths and pitches. A
`chord_progression` must then have one chord per bar of the cycle.

N45 (2026-11-23) uses it for two separate tables files, bass and melody (A aeolian over
Am7, 2-bar cycles, `period` 32), each with its own generation config
(`config/learned/N45_generate_{bass,melody}_Aaeolian.json`), with no chord step and no
filter.

**A fixed last note** (`final_note`; Ken, 2026-11-26). With `form: "walk"`, the config
key `final_note: {"bar": b, "step": s, "length": L}` puts the cycle's last note at
16th `s` of bar `b` (both counted from 1), held `L` grid steps. The walk then runs only
up to that note's start, and a walked note that would cross it is cut there (counted as
`notes_cut_at_final_note`). Its pitch still comes from `end_note`, at that position.
Without `final_note`, the walk's own last note is the end note. It cannot be combined
with `evolve`. First used for N58 "Piano (D)" (400 bars, 93.191 bpm), analysed as A
aeolian / Am7 with `base_pitch` 69, as 1-, 2-, 4- and 8-bar tables (`period` 16, 32, 64,
128), in `config/learned/N58_generate_piano_Aaeolian_{1,2,4,8}bar.json` (16 tracks ×
400 bars each, `end_note: "sampled"`, default seeds, seed 58, no chord step or filter):

| Cycle | Last note | Held |
|---|---|---|
| 8 bars | bar 8, beat 1 (`step` 1) | 1 bar (16) |
| 4 bars | bar 4, beat 3 (`step` 9) | 2 beats (8) |
| 2 bars | bar 2, beat 4 (`step` 13) | 1 beat (4) |
| 1 bar | the second 8th of beat 4 (`step` 15) | half a beat (2) |

At 8 bars (`period` 128), 33 of the 128 positions have fewer than 3 notes, so the
position tables there are thin.

---

## 6. Stage 2b: the melody, filled backwards (generate, per cycle)

The form fixes the **note slots** of the cycle: their positions and lengths. Pitches are
filled from the **last slot backwards**:

1. The last slot is C: the tonic, step 0 (MIDI `base_pitch`).
2. For each earlier slot j, moving backwards, `placed` is the list of pitches already
   chosen after j, in time order:
   - **Choose the order.** Use the largest k ≤ min(`max_context`, len(`placed`)) whose
     context (the first k entries of `placed`) has total weight ≥ `min_context_count`
     in `transitions[k]`.
   - **Weigh each candidate step p** that appears in that context's row:

     ```
     w(p) = transitions[k][ctx][p] ^ transition_exponent
          × pos_weight(s_j, p mod d) ^ position_exponent
     ```

     Both exponents default to **0.5** (Ken, 2026-11-22). Taking the square roots
     makes the product a geometric mean of the two tables. Without them, a preference
     both tables share (here, for the root) is counted twice. On the transposed N56
     tables, the plain product gave 60% A against 43% in the input; with square roots
     it gives 51%. Setting both exponents to 1 restores the plain product. Zero weights
     stay zero. The fallbacks below use the same exponent on the transition weights.

     where `s_j` is the slot's **actual position in the cycle** (mod `period`), not
     where its note came from in the pattern. `pos_weight` is
     `position_pitch[s_j][p mod d]` if that position's total weight is
     ≥ `min_position_count`. Otherwise it is 1 for every class, so a thin position
     puts no constraint on pitch.
   - **If every weight is zero** (the position table forbids everything the context
     allows), try the next lower qualifying order with the same rule. If every order
     gives all zeros, go back to the highest qualifying order and use the transition
     weights alone, ignoring the position table.
   - **Normalize and sample** with the cycle's melody random stream.
3. If no order qualifies at all, see Q2.

Each cycle is generated independently and ends on the tonic. A context never reaches
into the next cycle.

The output is ordinary forward time. Only the **order of filling** is backwards; nothing
is reversed afterwards.

### 6b. Chord progression (post-processing, optional)

After the melody is generated, and before anything is written, the cycle can be laid
over a **chord progression**, one chord per bar, e.g. `["Am7", "Em7", "Dm7", "C6"]`
(Ken, 2026-11-22).

- **Chord tones move.** A note whose pitch class belongs to the **source chord** (the
  chord in the tables header, Am7 here) moves to the **nearest tone of its bar's chord**,
  in either direction. A note already in the new chord stays.
- **Passing tones stay.** A note that was not a source-chord tone (B, D or F over Am7)
  is not touched.
- **Ties.** When the nearest target tone is equally far above and below, Ken's rules
  decide: root beats third, fifth beats third, fifth beats sixth, root beats seventh.
  For pairs he did not rank, the code uses the order root > fifth > third > sixth >
  seventh.
- **Which chord applies.** A note takes the chord of the bar it **starts** in. Notes never
  cross a bar line, because of the half-bar cut.
- **Effect.** This is not a transposition: only the chord tones shift, each by a
  semitone or a whole tone at most.

With source Am7 (A C E G), these are the only changes:

| Bar | Chord | Changes |
|---|---|---|
| 1 | Am7 | none |
| 2 | Em7 (E G B D) | A → B (tie G/B, fifth wins), C → B |
| 3 | Dm7 (D F A C) | E → F, G → A (tie F/A, fifth wins) |
| 4 | C6 (C E G A) | none: C6 has the same notes as Am7 |

With `end_note: "tonic"`, each cycle still ends on A5.

**Ending on the last chord's root** (`end_note: "last_chord_root"`; Ken, 2026-11-22).
This is the default whenever a `chord_progression` is given (Ken preferred these
endings in N59); without a progression the default is `"tonic"`.
The final note (the C half) becomes the **root of the progression's last chord**, in
the octave nearest `base_pitch`. For `… C6` with `base_pitch` A5, that is **C6** (MIDI
84): three semitones up, against nine down to C5. This changes where the backward fill
**starts**, not just the last note. The fill begins from that note (scale step 2 here)
instead of step 0, so the notes before it are drawn from the transitions that lead into
C, and the approach to the cadence fits. The root must be in the tables' scale.

**A sampled ending** (`end_note: "sampled"`; Ken, 2026-11-22). The final note is not
fixed. It is drawn first, before the rest of the cycle, like a note with no context:
from the overall pitch frequency of the input (every note's actual pitch, octave
included) times the position table at the last note's position (the C slot, beat 3,
for a half-bar form; wherever the walk's last note falls for `form: "walk"`), with the
usual exponents.
It is drawn with the cycle's melody seed, so with a half-bar form cycle n ends on the same note in every
track. The backward fill then starts from that note. The chord step treats it like any
other note. Over C6 nothing moves, because every Am7 tone is also a C6 tone, so a cycle
can end on a passing tone such as B or D. In the first run
(`…_bar2gap_freeend.json`, seed 60, 64 cycles), the endings were A5 ×27, E5 ×10, A4 ×6,
C6 ×6, C5 ×4, A6 ×3, E6 ×3, D5 ×3 and D6 ×2.

Chords are written as a root (C, F#, Bb, …) plus one of `""` (major), `m`, `6`,
`m6`, `7`, `m7`, `maj7`, `m7b5`, `dim`, `dim7`. Optional config keys:
`chord_progression` (a list, or a space-separated string, with as many chords as the
form has bars) and `source_chord` (default: the tables' `chord`). The code is in
`src/chords.py`, and `follow_progression()` in `src/learned_generator.py`.

### 6d. Dropping notes by start position (Ken, 2026-11-22)

This optional last filter runs after the chord step. The config key `drop_starts` is a
list of windows such as `{"bar": 2, "from": 10, "to": 16}`. Each window gives a bar of the
form and a range of grid steps (16ths) in that bar, counted from 1 and inclusive. Every
note that **starts** in a window is deleted, in every cycle of every track. A note that
starts earlier and is held into the window is kept, and so is its length. The melody,
rhythm and velocities are drawn exactly as without the filter: the matching notes are
simply removed afterwards, so the other notes are unchanged. The report gives the number
removed.

The first use is `config/learned/N59_generate_Aaeolian_Am7-Em7-Dm7-C6_bar2gap.json`, with
seed 60 (16 × 64). It drops 16ths 10–16 of bar 2, which leaves a gap at the end of bar 2:
1,728 of 23,360 notes were removed. All 16 tracks have a note on 16th 9, so in some
tracks that note sounds on into the gap.

### 6c. Velocity (Ken, 2026-11-22)

**Analysis** records each note's velocity in two more weight tables in the tables
file:

- `velocity`: for each start position (the 16th in the bar), the count of each
  velocity, e.g. `"0": {"34": 2, …, "75": 11, …}`;
- `velocity_by_length`: the same for each **start position and length**, keyed
  `"position length"`, e.g. `"0 2"` for 8th notes on the downbeat.

**Generation** gives each note a velocity drawn from those tables, using the usual
threshold `min_velocity_count` (default 3):

1. If the note's (position, length) cell has total weight ≥ 3, draw from it.
2. Otherwise, draw from its position's row, if that has ≥ 3.
3. Otherwise, draw from all positions pooled.

The position is the note's place in the output bar, and the length is its output
length (after the half-bar cut; the final C note is a quarter on beat 3).

**Velocities change every cycle** (Ken, 2026-11-22). They are keyed by track, like the
rhythm, and also by cycle, so they differ between tracks and every 4-bar cycle gets
fresh draws. This sounds less mechanical than repeating one pattern. Ken first asked for
rhythm-style keying (one pattern per track, repeated), then changed to this. They use
their own stream, `SeedSequence(seed, spawn_key=(3, m, n))`, separate from the rhythm
and melody streams. Switching velocities
on therefore leaves every pitch and rhythm unchanged.
`"velocity": "tables"` is the default; a number (1–127) gives every note that fixed
velocity, as before.

On `transposed.mid`, velocities run from 5 to 122 (median 79). 33 of the 56
(position, length) cells reach 3 notes, and they hold 98% of the notes. In the
16-track × 64-cycle N59 run, 99% of notes drew from a (position, length) cell and the
rest from their position.

### 6e. Evolving cycles (Ken, 2026-11-23)

With `"evolve": true` (only with `form: "walk"`), a track no longer repeats one rhythm
or draws a whole new melody each cycle. Cycle 0 is made as usual. Every later cycle
is the **previous cycle with one random stretch re-filled**, so changes pile up:

1. Draw a 16th j at random, uniformly over the cycle, and a half-width k with
   P(k) = (1 − r) r^k, r = `evolve_k_ratio` (default 0.5). So k = 0 half the time, 1 a
   quarter, 2 an eighth, and so on (mean 1). The range is a = max(0, j − k) to
   b = min(last 16th, j + k). (Ken, 2026-11-23. A first version took two uniform random
   positions as a and b; that changed about 40% of the cycle each time, too much.)
2. If a lies inside a note or a rest, move it back to the start of that note or rest.
3. If b lies inside a note, move the range end to that note's end; otherwise the range
   ends just after b.
4. Delete the notes in the range and re-walk the rhythm there (section 4), starting as
   if at the start of a cycle and cutting notes at the range end.
5. Draw the pitches of the new notes with the backward fill (section 6). The kept notes
   **after** the range are their context, so the stretch leads into what follows it;
   nothing ties it to the notes before it. If the range takes in the cycle's last note,
   the new last note is the end note (`end_note`).

Notes outside the range keep their start, length and pitch. Velocities are still drawn
every (track, cycle). The random numbers come from a fourth stream, `(4, m, n)`, so
cycle 0 is identical to the run without `evolve`.

Example (one bar shown, `x` start, `-` held, `.` rest). Previous: `x---x-x-..x-x---`.
The draw gives j = 7, k = 2, so a = 5, b = 9. Position 5 lies in the note starting at 4, so the range
starts at 4. Position 9 lies in a rest, so the range ends at 10. Steps 4–9 are re-walked
(e.g. `x-----` becomes `x-.x--`), and the notes from step 10 on are kept as context.

N45 run (16 × 160 at 96 bpm, seed 45): the range averaged about 4 sixteenths after
extending to note and rest boundaries, and about 1.3 notes were re-drawn per cycle. The
changes still accumulate: by cycle 160, about 58% of the bass notes and 96% of the
melody notes differ from cycle 0. Raising or lowering `evolve_k_ratio` widens or narrows
the stretch.

### 6f. Elaboration driven by the tables (Ken, 2026-11-26)

A separate method, selected with `"method": "elaborate"` in the generation config
(`src/learned_elaborator.py`; `scripts/generate_learned.py` runs it). It is the June
**elaboration workflow** (README, "Elaboration workflow"; `src/elaborator.py`) with its
hand-set weights replaced by the tables. Ken found the old process sounded better than
`evolve` (6e).

**Process.** The cycle is a grid of 16ths, each a note start, held, or silent; pitches are
scale steps. Cycle 0 is the tonic (step 0, A4 for N58) held over the whole cycle. With
`final_note` (as in section 5), it is held up to the final note, which is then the tonic
again; the final note and everything after it are **frozen**. Each later cycle is the
previous one plus `changes_per_cycle` edits, so the changes accumulate. By default
(`reverse_cycle_order: true`) the cycle order is then reversed, so a track runs from busy
to the single held note; `false` keeps the order, and `"both"` writes each track twice
(the reversed copy gets a `C` suffix).

**One edit** (`edit_rules: "input_states"`, the default). Pick a 16th d (uniformly over
the unfrozen 16ths: `change_weight: "uniform"`). Draw a **target state** for d from the
input's frequencies at that position: note start `n_start`, held `n_held`, silent
`n_free − n_start` (each over the total). Then make the edit that reaches it:

| Now \ target | Start | Held | Silent |
|---|---|---|---|
| Note start | **re-pitch** (must differ) | **join** into the note before (if one sounds at d−1) | the whole note becomes a **rest** |
| Held | **split**: re-sound the same pitch from d | nothing | **cut**: rest from d to the note's end |
| Rest | **new note**: pitch drawn, length from `lengths`, cut at the next note | **extend** the note before over d (if one sounds at d−1) | nothing |

If nothing changes, d and the target are drawn again, so each cycle differs from the one
before by exactly `changes_per_cycle` real edits. Because each position is pushed toward
its input mix of starts, holds and silences, the busy cycles settle near the input's
density.

**Why not the old rules as they were.** The first version mapped the old vectors onto the
tables the way the old configs derived them (start = `p_start`, change weight ∝ `p_start`,
rest = (1 − start)²). Those rules can split and extend notes but never join a note into
the one before, and the rest chance is near 0 on strong 16ths, so notes piled up: the
busy cycles had about twice the input's notes (2-bar: 19 against 10.4; 4-bar: 33 against
21), mostly runs of 16ths. Weighting the edit position by `n_start` instead barely helped.
The old rules remain available as `edit_rules: "old"` (with `change_weight` `"p_start"`
or `"starts"`, `change_exponent`, `rest_exponent` and `new_note_length`).

**Pitch** (`choose_pitch`, for re-pitches and new notes). The note before (`prev`) and the
notes after (`f1, f2, …`), within the cycle, are both used. In order, the first context
whose total weight is ≥ `min_context_count` is used:

1. **two-sided**, `prev → p → f1 … f(k−1)`, read as `transitions[k][(p, f1…)][prev]`,
   for k = `max_context` down to 2;
2. **backward**, `p → f1 … fk` (the rule of section 6), k = `max_context` down to 1;
3. **forward**, `prev → p` (`transitions[1][(p,)][prev]`);
4. the overall pitch frequency.

Each is multiplied by the position table, with the usual exponents (section 6). A
re-pitch excludes the current pitch.

**Velocities** are drawn per note for each (track, cycle), as in section 6c. **Seeds:** one
stream per track, `SeedSequence(seed, spawn_key=(7, m))`.

**Tables.** The method needs `onsets.n_held`, the number of times each position was
covered by a held note. The analysis writes it since 2026-11-26; older tables need
re-running.

**First runs** (N58 "Piano (D)", `config/learned/N58_elaborate_piano_Aaeolian_{1,2,4,8}bar.json`;
16 tracks × 400 bars, one edit per cycle, seed 58, the final notes of section 5).
Average notes per cycle against the input's average:

| Cycle | Input | Busiest (start of track) | Middle | 90% through | End |
|---|---|---|---|---|---|
| 1 bar | 5.2 | 6.7 | 6.6 | 6.6 | 2 |
| 2 bars | 10.4 | 11.6 | 12.2 | 9.2 | 2 |
| 4 bars | 20.9 | 22.4 | 18.9 | 8.2 | 2 |
| 8 bars | 41.7 | 25.2 | 16.2 | 5.0 | 2 |

With one edit per cycle, the 1- and 2-bar tracks reach the input's density early and
simplify only near the end. The 8-bar tracks, at 50 cycles, never reach it. Raising
`changes_per_cycle` for long cycles would fix that.

**Since 2026-11-26 every elaborate config runs 200 cycles** (Ken: more time to develop),
so the tracks are 200, 400, 800 and 1,600 bars long, at 96 bpm (N58's tempo from then
on). The busiest cycles then average 6.6, 11.6, 22.9 and 41.4 notes for 1, 2, 4 and 8
bars, so the 8-bar tracks now reach the input's density (41.7). Repeated pitches (35–44%) are close
to the input's 35%. **A** is commoner than in the input, and **D** and **E** rarer,
because splits and joins carry the starting A along until re-pitches change it.

---

## 7. Random seeds

All streams come from the one `seed`, using numpy `SeedSequence` spawn keys:

| Stream | Key | Changes with | Same for |
|---|---|---|---|
| rhythm | `SeedSequence(seed, spawn_key=(1, m))` | track m | every cycle of that track |
| melody | `SeedSequence(seed, spawn_key=(2, n))` | cycle n | cycle n of every track |
| velocity | `SeedSequence(seed, spawn_key=(3, m, n))` | track m and cycle n | nothing: every track and cycle differs |
| evolve (section 6e) | `SeedSequence(seed, spawn_key=(4, m, n))` | track m and cycle n | used only with `evolve`, for cycles n ≥ 1 |

So:

- **Within a track,** the rhythm pattern is the same in every cycle, while the melody
  changes from cycle to cycle.
- **Across tracks,** cycle n uses the same melody random numbers, but each track has a
  different rhythm. The slots differ in count and position, so the same random numbers
  meet different tables. The melodies are related only loosely and are not copies.
- Changing `num_cycles` or `num_tracks` does not change the tracks and cycles that
  already exist.

**Varying everything per cycle** (Ken, 2026-11-23). Two optional config keys change what
the rhythm and melody streams are keyed by:

| Key | Default | Other values |
|---|---|---|
| `rhythm_seed_by` | `"track"`: key `(1, m)`, one rhythm per track | `"track_cycle"`: key `(1, m, n)`, a new rhythm for every track and cycle; `"cycle"`: key `(5, n)`, a new rhythm every cycle, shared by all tracks |
| `melody_seed_by` | `"cycle"`: key `(2, n)`, shared by all tracks | `"track_cycle"`: key `(2, m, n)`, a new melody for every track and cycle; `"track"`: key `(6, m)`, the same melody random numbers in every cycle of a track |

With both set to `"track_cycle"`, every (track, cycle) has its own rhythm and notes, so
nothing repeats. The report then shows only each track's first-cycle rhythm, with the
range of notes per cycle. First used in
`config/learned/N45_generate_{bass,melody}_Aaeolian_varied.json`.

**Swapped roles** (Ken, 2026-11-25): `rhythm_seed_by: "cycle"` with
`melody_seed_by: "track"`. In cycle n every track plays the same rhythm, and the tracks
differ only in pitch. Each track reuses one melody stream, but because the slots change
every cycle, its melody still changes. These keys use their own tags (5 and 6), so the
streams are unrelated to the default ones. `evolve` still needs the default seeds.
Tried once on the N58 "Piano (D)" 2- and 4-bar tables (2026-11-25, then filed by
mistake as N59) and then dropped; no config is kept.

---

## 8. Output

- **One `.dawproject`** per run, with M instrument tracks. Each track holds one note
  clip, N cycles long. The tempo (`84`) and time signature come from the config, so
  nothing needs setting by hand in Cubase. All notes are velocity 100 on channel 1.
- **Names:** file names are long and descriptive with the piece prefix, e.g.
  `N59_learned_Ddorian_AB-AB-AA-BC_s59.dawproject`. Track names are short, because
  Cubase shows only a few characters (e.g. `N59 t1` … `N59 t8`).
- **Optional `.mid`** with the same content, written by the existing `src/midi_writer.py`.
- **A run report** beside the output listing the fallback counts from section 3.
- **Copying to Cubase:** on the desktop, also into `F:\Cubase Projects\N59\`.

**Building the DAWproject:** helpers can be reused from `~/video/curve_to_dawproject.py`
(`IdFactory`, `build_channel`, `build_metadata_xml`, `serialize`), and the XSDs in
`~/video/schema/dawproject/` can be used for validation. That code only writes volume
automation on audio tracks. **Note clips on instrument tracks are new.** Before building
the full writer, a small file with one track and a few notes should be tested with
`File > Import > DAWproject` in Cubase 15.

---

## 9. Code

| File | Role | Status |
|---|---|---|
| `scripts/analyze_midi.py` | `analyze`: config + MIDI → tables `.json`, prints a report | done |
| `src/learner.py` | Config check, cleaning, scale steps, tables T1–T4, tables-file writer | done |
| `scripts/generate_learned.py` | `generate`: config + tables → `.dawproject` (+ `.mid`), prints a report | done |
| `src/learned_generator.py` | Tables check, rhythm walk, form, backward fill | done |
| `src/learned_elaborator.py` | Elaboration method (section 6f); `generate_learned.py` uses it when the config has `"method": "elaborate"` | done |
| `src/dawproject_writer.py` | Note-clip DAWproject writer, schema-checked against `schema/dawproject/Project.xsd` | done; Cubase import not yet tested |
| `config/learned/` | `N59_analyze_transposed_Aaeolian.json`, `N59_generate_Aaeolian.json` | |

Run the analysis with:

```
python scripts/analyze_midi.py   config/learned/N59_analyze_transposed_Aaeolian.json
python scripts/generate_learned.py config/learned/N59_generate_Aaeolian.json [--seed N]
```

**Generation config keys:** `tables`, `output_dir`, `form`, `num_cycles`, `num_tracks`,
`seed`, `tempo`, and optionally `max_context` (default 3), `min_context_count` (3),
`min_position_count` (3), `transition_exponent` and `position_exponent` (0.5 each),
`chord_progression`, `source_chord` and `end_note` (section 6b), `velocity` (`"tables"`, or a fixed
1–127) and `min_velocity_count` (3) (section 6c), `drop_starts` (section 6d), `evolve` (section 6e), `rhythm_seed_by` and `melody_seed_by` (section 7), `track_name_prefix` (the config name's first
`_` part, e.g. `N59`) and `write_mid` (true). The grid, key and `base_pitch` come from
the tables file. Output files are named `<config stem>_s<seed>_<timestamp>`.

The Q2 proposal is implemented as written, and the report counts how often it fires.
When no context meets `min_context_count`, generate uses the 1-note context with any
weight above zero. If that context was never seen, it uses the overall pitch frequency
times the position table.

**Analysis config keys:** `input_midi`, `output_path`, `root`, `mode` (or `scale_semitones`),
`chord`, `base_pitch`, `divisions_per_beat`, `beats_per_bar`, `bars_per_cycle`,
`input_quantization`, and optionally `period` (default one bar), `snap_priority`
(default `[0,2,4,5]`), `max_context` (default 3, the highest order written) and
`skip_silent_bars` (default 2; section 3, T1).
`base_pitch` must be a `root` note.

**Details of the written file:**
- **Header extras:** besides the layout in section 3, the header records `created`,
  `source_tempo_bpm`, `source_time_signature`, `input_quantization`, `snap_priority` and
  `max_context`, plus a `cleaning` line with the counts from section 2.
- **Rows:** `lengths` and `position_pitch` list every position, even empty ones.
  `position_pitch` lists every degree class, zeros included, so any weight can be edited
  in place.
- **Rounding:** a note end exactly halfway between grid lines rounds up.

Patterns to reuse from this repo: config loading and validation as in `src/config.py`,
`PPQN = 960` and `.mid` writing from `src/midi_writer.py`, and the CLI layout of
`scripts/generate_*.py` (config path, `--seed`).

---

## 10. Worked example (toy data)

A two-bar input in D dorian, `base_pitch = 62`. To make the tiny tables do something,
this example uses **thresholds of 1** in place of 3.

| Bar | Start | Length | Note | Step |
|---|---|---|---|---|
| 1 | 0 | 4 | A4 | 4 |
| 1 | 4 | 2 | F4 | 2 |
| 1 | 6 | 2 | E4 | 1 |
| 1 | 8 | 4 | D4 | 0 |
| 1 | 12 | — | (rest, 2) | — |
| 1 | 14 | 2 | F#4 → F4 | 2 |
| 2 | 0 | 4 | A4 | 4 |
| 2 | 4 | 4 | G4 | 3 |
| 2 | 8 | 2 | F4 | 2 |
| 2 | 10 | 2 | E4 | 1 |
| 2 | 12 | 4 | D4 | 0 |

F#4 snaps to F4 because F is the 3rd and G is the 4th. The sequence of steps is
`4 2 1 0 2 4 3 2 1 0`.

**T1.** Positions with a free line: bar 1 at 0, 4, 6, 8, 12, 13, 14; bar 2 at 0, 4, 8,
10, 12. Starts: bar 1 at 0, 4, 6, 8, 14; bar 2 at 0, 4, 8, 10, 12. So
`p_start = 1` at 0, 4, 6, 8, 10 and 14; `0.5` at 12; `0` everywhere else.

**T2.** `0:{4:2}`, `4:{2:1,4:1}`, `6:{2:1}`, `8:{4:1,2:1}`, `10:{2:1}`, `12:{4:1}`, `14:{2:1}`.

**T3** (degree classes). `0:{4:2}`, `4:{2:1,3:1}`, `6:{1:1}`, `8:{0:1,2:1}`, `10:{1:1}`,
`12:{0:1}`, `14:{2:1}`.

**T4** (following context → preceding step):

| k | Context | Preceding |
|---|---|---|
| 1 | `2` | `{4:1, 0:1, 3:1}` |
| 1 | `1` | `{2:2}` |
| 1 | `0` | `{1:2}` |
| 1 | `4` | `{2:1}` |
| 1 | `3` | `{4:1}` |
| 2 | `2 1` | `{4:1, 3:1}` |
| 2 | `1 0` | `{2:2}` |
| 2 | `0 2` | `{1:1}` |
| 2 | `2 4` | `{0:1}` |
| 2 | `4 3` | `{2:1}` |
| 2 | `3 2` | `{4:1}` |
| 3 | `2 1 0` | `{4:1, 3:1}` |
| 3 | `1 0 2` | `{2:1}` |
| 3 | `0 2 4` | `{1:1}` |
| 3 | `2 4 3` | `{0:1}` |
| 3 | `4 3 2` | `{2:1}` |
| 3 | `3 2 1` | `{4:1}` |

**Rhythm walk (one possible draw).** s=0: note, length 4. s=4: note, length 2.
s=6: note, length 2. s=8: note, length 4. s=12: `p_start = 0.5`, drawn as a rest.
s=13: `p_start = 0`, rest. s=14: note, length 2.
So **A** = notes at 0 (4), 4 (2), 6 (2), and **B** = notes at 8 (4), 14 (2).

**Cycle `AB AB AA BC`, slots by bar** (position: length):

| Bar | Slots |
|---|---|
| 1 | 0:4, 4:2, 6:2, 8:4, 14:2 |
| 2 | 0:4, 4:2, 6:2, 8:4, 14:2 |
| 3 | 0:4, 4:2, 6:2, 8:4, 12:2, 14:2 |
| 4 | 0:4, 6:2, **8:4 = C (tonic)** |

**Backward fill, last four slots:**

1. Bar 4, pos 8 — C: step **0** (D4).
2. Bar 4, pos 6 — `placed = [0]`. k=1, context `0` → `{1:2}`. T3[6] = `{1:1}`, so class 1
   gets weight 1. w(1) = 2 → step **1** (E4), certain.
3. Bar 4, pos 0 — `placed = [1, 0]`. k=2, context `1 0` → `{2:2}`. T3[0] = `{4:2}`, so
   class 2 gets weight 0 and every weight is zero. Lower order k=1, context `1` → `{2:2}`:
   also zero. So use k=2 without T3: step **2** (F4), certain.
4. Bar 3, pos 14 — `placed = [2, 1, 0]`. k=3, context `2 1 0` → `{4:1, 3:1}`. T3[14] =
   `{2:1}`, so both are zero. k=2 (`2 1` → `{4:1,3:1}`) and k=1 (`2` → `{4,0,3}`) are zero
   too. So use k=3 without T3: step 4 or 3, **50/50** from the melody stream.

The end of the cycle reads (A4 or G4) · F4 · E4 · D4. Step 3 shows a thin, sharply
peaked position table overruling everything. With the real threshold of 3, those
positions would count as thin and not constrain pitch at all.

---

## 11. Questions and answers

Q1: "Ignore the position table if < 3 choices." Does that mean fewer than 3 **notes**
    observed at that 16th (as written above: total weight < `min_position_count`) or fewer
    than 3 **distinct pitch classes**?
A: Fewer than 3 **notes** observed at that 16th (total weight < `min_position_count`).
   Implemented that way. With the transposed N56 input, every 16th has at least 3.

Q2: If **no** order has a context meeting `min_context_count` (possible early in the
    fill, when only the tonic is placed and it is rare in the input), what then?
    Proposed: use k=1 with any total > 0; if the context was never seen at all, use the
    position table times the input's overall pitch frequency.
A: Implemented as proposed (section 9): k=1 with any total > 0; if never seen, the
   overall pitch frequency times the position table. The report counts how often it
   fires: 26 times in about 22,500 choices in the N59 run.

Q3: Should `period` default to 16 (pool all bars) or 64 (keep the four bars of a cycle
    apart)? Proposed: 16, and maybe allow different periods for T1/T2 and T3 later.
A: 16 (one bar). With 64, half the positions have 4 or fewer notes in the test data
   (section 2). `period` stays a setting.

Q4: Should the chord play any part (e.g. in snapping, or a bonus for chord tones on
    strong beats), or is it only recorded for now?
A: Yes, in two ways, both after the melody is drawn (section 6b): the chord
   progression moves source-chord tones to the nearest tone of each bar's chord, and
   `end_note: "last_chord_root"` ends each cycle on the last chord's root. The chord
   plays no part in the analysis itself.

Q5: Should output pitches be limited to a range (e.g. the input's lowest to highest
    note)? The transition tables only produce pitches seen in the input, so the range
    is implicitly limited already.
A: Limit by the input (Ken, 2026-11-22), and no extra setting is needed. The transition tables only
   produce pitches seen in the input. **Exception:** the chord-progression step can move a
   note up to 2 semitones past the input's top or bottom. In the N59 run, 6 of about
   23,500 notes went from A6 (the input's top note) to B6 over Em7. Ken decided to
   leave this as it is (2026-11-22).

Q6: Does every cycle end on the tonic at `base_pitch`, or should the octave (or the
    ending note itself) be a parameter?
A: A parameter: `end_note` is `"tonic"` (step 0 at `base_pitch`; the default without a
   chord progression) or
   `"last_chord_root"` (the last chord's root, in the octave nearest `base_pitch`; C6 in
   N59; the default with a chord progression), or `"sampled"` (drawn from the tables each cycle). See section 6b.

Q7: Is a rhythm pattern with very few notes, or none (e.g. an empty B), acceptable?
    Or should the walk be redrawn until each half has at least one note?
A: Leave as is (Ken, 2026-11-22). Sparse patterns are acceptable; Ken picks tracks by
   ear afterwards.

Q8: Should the first note of a cycle relate to the end of the previous cycle (a
    transition across the cycle boundary), or are cycles fully independent as above?
A: No (Ken, 2026-11-22). Cycles stay fully independent.

Q9: The tables are in scale steps, so they don't depend on the key. Should `generate`
    be able to render them in a **different root or mode** from the input? For example,
    tables learned from the E-phrygian N56 file could be played as D dorian for N59: the
    same contours and degree patterns, but with major-6th and major-2nd colour. Proposed:
    `generate` takes optional `root` / `mode` / `base_pitch`, defaulting to the tables
    header.
A: No (Ken, 2026-11-22). Generation renders in the tables' own key. To use another key,
   analyse an input transposed into it, as was done for `transposed.mid`.
