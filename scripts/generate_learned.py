"""Learned workflow, stage 2: generate melodies from a tables file.

    python scripts/generate_learned.py config/learned/<name>.json [--seed N]

Reads the tables file named in the config (never the MIDI), generates
num_tracks tracks of num_cycles cycles each (docs/learned_workflow.md,
sections 4-7) and writes a .dawproject (plus a .mid if write_mid is true)
into output_dir, named <config stem>_s<seed>_<timestamp>.
"""

import argparse
from collections import Counter
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import dawproject_writer
import learned_generator as lg
import midi_writer

PC = "C C# D D# E F F# G G# A A# B".split()


def bar_picture(bar, total, split):
    """A one-line drawing of the rhythm: 'x' = note start, '-' = held, '.' = rest,
    with '|' every split steps."""
    cells = ["."] * total
    for s, L in bar:
        cells[s] = "x"
        for t in range(s + 1, s + L):
            cells[t] = "-"
    return " | ".join("".join(cells[i:i + split]) for i in range(0, total, split))


def main():
    parser = argparse.ArgumentParser(description="Generate melodies from learned tables.")
    parser.add_argument("config_path", help="generation config, e.g. config/learned/N59_generate_Aaeolian.json")
    parser.add_argument("--seed", type=int, default=None, help="overrides 'seed' in the config")
    args = parser.parse_args()

    cfg = lg.load_generation_config(args.config_path)
    if args.seed is not None:
        cfg["seed"] = args.seed
    tables = lg.load_tables(cfg["tables"])
    tracks, cycle_steps, stats, moves = lg.generate(cfg, tables)

    dpb = tables["divisions_per_beat"]
    steps_per_bar = dpb * tables["beats_per_bar"]
    stem = Path(args.config_path).stem
    out_stem = f"{stem}_s{cfg['seed']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir = Path(cfg["output_dir"])
    names = [f"{cfg['track_name_prefix']} t{m + 1}" for m in range(len(tracks))]
    total_beats = cfg["num_cycles"] * cycle_steps / dpb

    daw_tracks = [(name, total_beats, [(s / dpb, L / dpb, p, v) for s, L, p, v in t["notes"]])
                  for name, t in zip(names, tracks)]
    daw_path = out_dir / f"{out_stem}.dawproject"
    key = f"{tables['root']} {tables['mode'] or tables['scale']}"
    dawproject_writer.write_dawproject(
        daw_path, daw_tracks, cfg["tempo"], tables["beats_per_bar"], 4,
        title=out_stem,
        comment=(f"Learned workflow: {cfg['num_tracks']} tracks x {cfg['num_cycles']} cycles, "
                 f"form {cfg['form']}, seed {cfg['seed']}, tables {cfg['tables']} ({key})"
                 + (f", chords {' '.join(cfg['chord_progression'])}" if cfg["chord_progression"] else "")))

    if cfg["write_mid"]:
        ticks = midi_writer.PPQN // dpb
        midi_tracks = [[midi_writer.NoteEvent(s * ticks, L * ticks, p, v) for s, L, p, v in t["notes"]]
                       for t in tracks]
        midi_writer.write_midi(midi_tracks, str(out_dir / f"{out_stem}.mid"), names)

    # Report
    print(f"Tables  {cfg['tables']}  ({key}, chord {tables['chord']}, base_pitch {tables['base_pitch']})")
    last_notes = {}   # (track, cycle) -> final MIDI pitch
    for m, t in enumerate(tracks):
        for s, _, p, _ in t["notes"]:
            last_notes[(m, s // cycle_steps)] = p
    ends = Counter(f"{PC[p % 12]}{p // 12 - 1}" for p in last_notes.values())
    ending = (f"ending on {', '.join(f'{k} x{v}' for k, v in ends.most_common())} (sampled)"
              if cfg["end_note"] == "sampled" else f"ending on {next(iter(ends))}")
    print(f"Form    {cfg['form']}  ({cycle_steps // steps_per_bar} bars), {cfg['num_cycles']} cycles, "
          f"{cfg['tempo']} bpm, seed {cfg['seed']}, {ending}")
    walk = cfg["form_bars"] is None
    print("Rhythms (x = note start, - = held, . = rest; " + ("bars" if walk else "A | B") + "):")
    for name, t in zip(names, tracks):
        pic = (bar_picture(t["bar"], cycle_steps, steps_per_bar) if walk
               else bar_picture(t["bar"], steps_per_bar, steps_per_bar // 2))
        print(f"  {name:{max(8, max(map(len, names)))}s} {pic}   {t['slots_per_cycle']} notes/cycle")
    dropped = stats.pop("dropped by drop_starts", 0)
    print("Choices " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
    if cfg["chord_progression"]:
        source = cfg["source_chord"] or tables["chord"]
        print(f"Chords  {' '.join(cfg['chord_progression'])} (one per bar; {source} tones moved, "
              f"passing tones kept)")
        for chord in dict.fromkeys(cfg["chord_progression"]):
            moved = [(f, t, n) for (c, f, t), n in moves.items() if c == chord]
            print(f"  {chord:6s} " + (", ".join(f"{PC[f]}->{PC[t]} {n}" for f, t, n in sorted(moved))
                                      or "no notes moved"))
    for w in cfg["drop_starts"]:
        print(f"Dropped notes starting on steps {w['from']}-{w['to']} of bar {w['bar']}", end="; ")
    if cfg["drop_starts"]:
        print(f"{dropped} notes removed")
    print(f"Wrote   {daw_path}" + (f"\n        {out_dir / out_stem}.mid" if cfg["write_mid"] else ""))


if __name__ == "__main__":
    main()
