"""CLI entry point for music-automation MIDI generation."""

import argparse
import json
from pathlib import Path

import numpy as np

from generator import generate_track
from midi_output import events_to_midi
from tables import load_tables


def main():
    parser = argparse.ArgumentParser(description="Generate MIDI tracks from probability tables")
    parser.add_argument("--config", default="config.json", help="Path to config JSON file")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--output", default=None, help="Output MIDI file path")
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

    output_path = args.output or f"data/output/output.mid"
    events_to_midi(tracks, config["tempo"], output_path)
    print(f"MIDI file saved to {output_path}")


if __name__ == "__main__":
    main()
