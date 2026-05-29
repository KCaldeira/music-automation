"""CLI entry point — load config, generate tracks, write MIDI, print summary."""

import argparse
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import config as config_loader
import generator
import midi_writer


def main():
    parser = argparse.ArgumentParser(description="Generate a random MIDI file from a JSON config.")
    parser.add_argument("config_path", help="path to JSON config (e.g. config/test.json)")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for reproducible output (overrides 'seed' in the config)")
    args = parser.parse_args()

    t0 = time.perf_counter()
    cfg = config_loader.load_config(args.config_path)

    # CLI --seed wins; otherwise fall back to the config's "seed" (may be None = random).
    seed = args.seed if args.seed is not None else cfg["seed"]

    if midi_writer.PPQN % cfg["divisions_per_beat"] != 0:
        raise ValueError(
            f"divisions_per_beat ({cfg['divisions_per_beat']}) must divide PPQN "
            f"({midi_writer.PPQN}) evenly. Valid values: 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 16, ..."
        )

    rng = np.random.default_rng(seed)

    forward = [generator.generate_track(cfg, rng) for _ in range(cfg["num_tracks"])]

    steps_per_cycle = cfg["divisions_per_beat"] * cfg["beats_per_bar"] * cfg["bars_per_cycle"]
    ticks_per_step = midi_writer.PPQN // cfg["divisions_per_beat"]

    # track_direction selects which tracks are written; reversed tracks are
    # always derived from the forward tracks, so forward is generated regardless.
    direction = cfg["track_direction"]
    want_forward = direction in ("forward", "both")
    want_reversed = direction in ("reversed", "both")

    reversed_tracks = []
    if want_reversed:
        reversed_tracks = [
            generator.reverse_track(t, steps_per_cycle,
                                    cfg["reversal_last_note_start_step"])
            for (t, _) in forward
        ]

    stem = Path(args.config_path).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(cfg["output_dir"]) / f"{stem}_{timestamp}.mid"

    # Track names: the part of the config filename after the last "_", then the
    # track number (with "R" appended for the reversed companion tracks).
    name_base = stem.rsplit("_", 1)[-1]
    midi_tracks, track_names = [], []
    if want_forward:
        for i, (t, _) in enumerate(forward, start=1):
            midi_tracks.append(midi_writer.track_to_note_events(t, steps_per_cycle, ticks_per_step))
            track_names.append(f"{name_base} {i}")
    if want_reversed:
        for i, t in enumerate(reversed_tracks, start=1):
            midi_tracks.append(midi_writer.track_to_note_events(t, steps_per_cycle, ticks_per_step))
            track_names.append(f"{name_base} {i}R")

    midi_writer.write_midi(midi_tracks, cfg["tempo"], str(output_path), track_names)

    elapsed = time.perf_counter() - t0
    print_summary(forward if want_forward else [], reversed_tracks, output_path, seed, elapsed)


def _count(track):
    notes = rests = 0
    for cycle in track:
        for ev in cycle:
            if ev.pitch is None:
                rests += 1
            else:
                notes += 1
    return notes, rests


def print_summary(forward, reversed_tracks, output_path, seed, elapsed):
    print(f"Output: {output_path}")
    print(f"Seed:   {seed if seed is not None else 'random'}")
    print()

    total_notes = total_rests = 0
    for i, (track, num_terminated) in enumerate(forward, start=1):
        notes, rests = _count(track)
        cycles = len(track)
        flag = f"  [{num_terminated} cycle(s) cut short]" if num_terminated else ""
        print(f"  Forward track {i}: {cycles} cycle(s), {notes} note(s), {rests} rest(s){flag}")
        total_notes += notes
        total_rests += rests

    if reversed_tracks:
        print()
        for i, track in enumerate(reversed_tracks, start=1):
            notes, rests = _count(track)
            cycles = len(track)
            print(f"  Reversed track {i}: {cycles} cycle(s), {notes} note(s), {rests} rest(s)")
            total_notes += notes
            total_rests += rests

    print()
    print(f"Total: {total_notes} note(s), {total_rests} rest(s) across all MIDI tracks")
    print(f"Elapsed: {elapsed:.3f}s")


if __name__ == "__main__":
    main()
