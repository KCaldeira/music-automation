# CLAUDE.md

Guidance for Claude Code in this repository. It generates melodic material for Ken's
pieces (N45, N58, N59, …) and hands it to Cubase. Full documentation: `README.md`
(stochastic and elaboration workflows) and `docs/learned_workflow.md` (the learned
workflow, including the table-driven elaboration of section 6f).

## Code philosophy

- **Fail fast.** No `try/except` to hide errors or supply fallbacks; let the code
  fail so the real problem shows. Prefer a crash to silently wrong music.
- **Root causes, not patches.** Understand why something is wrong before changing it.
- **Validate inputs on load** (configs and tables files) and give a clear message
  naming the file and the key.
- **Report what happened.** Generators print a run report (fallback counts, notes per
  cycle, …) so a run can be checked without opening Cubase.

## How Ken wants pipelines built

- **Analysis and generation are separate programs**, joined by a hand-editable
  intermediate file. The learned workflow's tables JSON stores raw **weights
  (counts)**, not normalized probabilities, so Ken can tweak them by hand. Generation
  never reads the MIDI. (Ken, 2026-11-22.)
- **Output is `.dawproject`** (Cubase: `File > Import > DAWproject`), written by
  `src/dawproject_writer.py`. A `.mid` is optional (`write_mid`). DAWproject files
  carry tempo, time signature and track names; this repo's `.mid` files carry no
  tempo.
- **Names:** track names are short, because Cubase shows only a few characters
  (`N58 elab t3`, `2bar t5`). File names are long and descriptive, and an output
  file is named `<config stem>_s<seed>_<timestamp>`, so the config's name is what
  describes the run.
- **New options keep old runs reproducible:** add a config key with a default that
  reproduces the earlier behaviour, document it in `docs/learned_workflow.md`, and
  record there what was tried and why (Ken's decisions are dated in that file).

## Files and folders

- **Inputs:** copy the MIDI Ken names (usually in his Cubase project folder) into
  `data/input/<piece>/`. `*.mid` and `*.dawproject` are gitignored.
- **Outputs:** by default `data/output/`. When Ken asks for output to go to the
  piece's Cubase folder (as for N58), set the config's `output_dir` to it:
  - desktop (`SmallPinkDot`): `/mnt/f/Cubase Projects/<piece>/`
  - laptop (`ProLaptopCompact`): `/mnt/d/<piece>/`

  These paths differ between the machines, so a config with one machine's
  `output_dir` fails on the other. Check `hostname` and adjust. Windows refuses to
  overwrite a file Cubase has open ("Permission denied"); use a new name.
- **Merging for one import:** `scripts/merge_dawproject.py OUT IN1 IN2 …` puts the
  tracks of several `.dawproject` files into one, with each track name prefixed by
  its file's cycle length (e.g. `4bar t7`).

## Pieces

| Piece | Input | Key / chord | `base_pitch` | Tempo for output |
|---|---|---|---|---|
| N58 | `Piano (D).mid` (400 bars, saved at 93.191 bpm) | A aeolian / Am7 | 69 | **96 bpm** (Ken, 2026-11-26) |

For other pieces, take the tempo from their latest configs, or ask. A `.dawproject`
import overwrites the target project's tempo and time signature, so the config's
`tempo` must match the Cubase project.

## Cubase (16.0.5, Windows)

Ken uses **Cubase 16.0.5** since 2026-11-26; assume it for any DAWproject or MIDI
question. The points below were established on Cubase 15.0.30 (mostly in `~/video`);
if an import misbehaves, re-check them on 16 before redesigning anything.

- **Note names: Cubase calls MIDI 60 C3**, one octave below the scientific names used
  in `docs/` (MIDI 69 is A3 in Cubase, A4 in the docs). When telling Ken about
  notes, give the MIDI number and Cubase's name.
- **DAWproject must-haves** (all optional in the schema, all required by Cubase):
  `contentType` on every `Track`, `Transport/Tempo`, and `Transport/TimeSignature`.
  Schema-valid is not the same as loadable, so confirm any structural change to the
  writer with a real import.
- **MIDI exported from Cubase:** Play Probability is applied on export (a note below
  100% is kept or dropped at random), and muted or disabled tracks are left out.
  Bear this in mind when an input file seems to be missing notes.

## Quick reference

```bash
# Learned workflow: analyze a MIDI file into tables, then generate
.venv/bin/python scripts/analyze_midi.py    config/learned/N58_analyze_piano_Aaeolian_4bar.json
.venv/bin/python scripts/generate_learned.py config/learned/N58_generate_piano_Aaeolian_4bar.json   # rhythm walk + backward fill
.venv/bin/python scripts/generate_learned.py config/learned/N58_elaborate_piano_Aaeolian_4bar.json  # "method": "elaborate"

# Older workflows (hand-set weights; README)
.venv/bin/python scripts/generate_stochastic.py  config/stochastic/<name>.json  [--seed N]
.venv/bin/python scripts/generate_elaboration.py config/elaboration/<name>.json [--seed N]
```

Code: `src/learner.py` (analysis), `src/learned_generator.py` (tables loader, rhythm
walk, backward fill), `src/learned_elaborator.py` (table-driven elaboration),
`src/elaborator.py` and `src/generator.py` (older workflows), `src/dawproject_writer.py`.
