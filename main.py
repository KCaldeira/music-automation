"""CLI entry point for music-automation MIDI generation."""

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from cycle_sorter import sort_and_redistribute_cycles, write_cycle_stats_csv
from generator import generate_track
from midi_output import events_to_midi
from tables import load_tables


def main():
    parser = argparse.ArgumentParser(description="Generate MIDI tracks from probability tables")
    parser.add_argument("--config", default="config.json", help="Path to config JSON file")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    divisions_per_cycle = (
        config["divisions_per_beat"]
        * config["beats_per_bar"]
        * config["bars_per_cycle"]
    )
    divisions_per_bar = config["divisions_per_beat"] * config["beats_per_bar"]

    tables = load_tables(config["table_dir"], divisions_per_cycle, divisions_per_bar)
    print(f"Loaded tables from {config['table_dir']}")

    rng = np.random.default_rng(args.seed)

    tracks = []
    for i in range(config["num_tracks"]):
        events = generate_track(config, tables, rng)
        print(f"Track {i+1}: {len(events)} note events")
        tracks.append(events)

    # Calculate ticks_per_cycle
    ticks_per_division = 480 // config["divisions_per_beat"]
    ticks_per_cycle = divisions_per_cycle * ticks_per_division

    # Generate timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Output to same directory as input tables
    output_dir = Path(config["table_dir"])
    original_path = output_dir / f"output_{timestamp}_original.mid"
    sorted_path = output_dir / f"output_{timestamp}_sorted.mid"

    # Write original tracks
    events_to_midi(tracks, config["tempo"], str(original_path))
    print(f"Original MIDI saved to {original_path}")

    # Sort and write sorted tracks
    sorted_tracks = sort_and_redistribute_cycles(
        tracks, ticks_per_cycle, config["total_cycles"]
    )
    events_to_midi(sorted_tracks, config["tempo"], str(sorted_path))
    print(f"Sorted MIDI saved to {sorted_path}")

    # Write cycle statistics CSV
    stats_path = output_dir / f"output_{timestamp}_cycle_stats.csv"
    write_cycle_stats_csv(
        sorted_tracks, ticks_per_cycle, config["total_cycles"], stats_path
    )
    print(f"Cycle stats saved to {stats_path}")


if __name__ == "__main__":
    main()
