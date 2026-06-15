"""CLI entry point for the elaboration pathway — load config, generate tracks,
write MIDI, print summary."""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# Make the shared modules in ../src importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config as config_loader
import elaborator
import midi_writer


def main():
    parser = argparse.ArgumentParser(
        description="Generate a MIDI file by progressive elaboration from a JSON config.")
    parser.add_argument("config_path",
                        help="path to JSON config (e.g. config/elaboration/test.json)")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for reproducible output (overrides 'seed' in the config)")
    args = parser.parse_args()

    t0 = time.perf_counter()
    cfg = config_loader.load_elaboration_config(args.config_path)

    # CLI --seed wins; otherwise fall back to the config's "seed" (may be None = random).
    seed = args.seed if args.seed is not None else cfg["seed"]

    if midi_writer.PPQN % cfg["divisions_per_beat"] != 0:
        raise ValueError(
            f"divisions_per_beat ({cfg['divisions_per_beat']}) must divide PPQN "
            f"({midi_writer.PPQN}) evenly. Valid values: 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 16, ..."
        )

    rng = np.random.default_rng(seed)

    steps_per_cycle = cfg["divisions_per_beat"] * cfg["beats_per_bar"] * cfg["bars_per_cycle"]
    ticks_per_step = midi_writer.PPQN // cfg["divisions_per_beat"]

    # Each track is a list[Grid]; convert to a Track (list[list[StepEvent]]).
    tracks = [elaborator.generate_track(cfg, rng) for _ in range(cfg["num_tracks"])]
    event_tracks = [[elaborator.grid_to_stepevents(g) for g in track] for track in tracks]

    stem = Path(args.config_path).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(cfg["output_dir"]) / f"{stem}_{timestamp}.mid"

    # Track names: the config filename (no extension), then the track number.
    name_base = stem
    midi_tracks, track_names = [], []
    for i, t in enumerate(event_tracks, start=1):
        midi_tracks.append(midi_writer.track_to_note_events(t, steps_per_cycle, ticks_per_step))
        track_names.append(f"{name_base} {i}")

    midi_writer.write_midi(midi_tracks, str(output_path), track_names)

    elapsed = time.perf_counter() - t0
    print_summary(event_tracks, output_path, seed, elapsed)


def _count(track):
    notes = rests = 0
    for cycle in track:
        for ev in cycle:
            if ev.pitch is None:
                rests += 1
            else:
                notes += 1
    return notes, rests


def print_summary(event_tracks, output_path, seed, elapsed):
    print(f"Output: {output_path}")
    print(f"Seed:   {seed if seed is not None else 'random'}")
    print()

    total_notes = total_rests = 0
    for i, track in enumerate(event_tracks, start=1):
        notes, rests = _count(track)
        cycles = len(track)
        print(f"  Track {i}: {cycles} cycle(s), {notes} note(s), {rests} rest(s)")
        total_notes += notes
        total_rests += rests

    print()
    print(f"Total: {total_notes} note(s), {total_rests} rest(s) across all MIDI tracks")
    print(f"Elapsed: {elapsed:.3f}s")


if __name__ == "__main__":
    main()
