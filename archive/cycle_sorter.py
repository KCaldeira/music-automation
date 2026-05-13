"""Cycle-based sorting and redistribution of note events."""

import csv
from pathlib import Path
from statistics import mean, median, stdev

from generator import NoteEvent


def get_cycle_index(event: NoteEvent, ticks_per_cycle: int) -> int:
    """Return the cycle index (0-based) for a given note event."""
    return event.start_tick // ticks_per_cycle


def group_events_by_cycle(
    events: list[NoteEvent],
    ticks_per_cycle: int,
    total_cycles: int,
) -> dict[int, list[NoteEvent]]:
    """Group note events by cycle index."""
    cycles: dict[int, list[NoteEvent]] = {i: [] for i in range(total_cycles)}
    for event in events:
        cycle_idx = get_cycle_index(event, ticks_per_cycle)
        if 0 <= cycle_idx < total_cycles:
            cycles[cycle_idx].append(event)
    return cycles


def compute_duration_weighted_mean_pitch(events: list[NoteEvent]) -> float | None:
    """Compute duration-weighted mean pitch. Returns None if no notes."""
    if not events:
        return None
    total_weighted_pitch = sum(e.pitch * e.duration_ticks for e in events)
    total_duration = sum(e.duration_ticks for e in events)
    if total_duration == 0:
        return None
    return total_weighted_pitch / total_duration


def sort_and_redistribute_cycles(
    tracks: list[list[NoteEvent]],
    ticks_per_cycle: int,
    total_cycles: int,
) -> list[list[NoteEvent]]:
    """Redistribute cycles so highest mean pitch goes to track 0."""
    num_tracks = len(tracks)

    track_cycles = [
        group_events_by_cycle(track, ticks_per_cycle, total_cycles)
        for track in tracks
    ]

    sorted_tracks: list[list[NoteEvent]] = [[] for _ in range(num_tracks)]

    for cycle_idx in range(total_cycles):
        cycle_data = []
        for track_idx, cycles_dict in enumerate(track_cycles):
            events = cycles_dict[cycle_idx]
            mean_pitch = compute_duration_weighted_mean_pitch(events)
            cycle_data.append((track_idx, events, mean_pitch))

        # Sort: highest pitch first, None last
        def sort_key(item):
            _, _, mean_pitch = item
            if mean_pitch is None:
                return (1, 0.0)
            return (0, -mean_pitch)

        cycle_data.sort(key=sort_key)

        for new_track_idx, (_, events, _) in enumerate(cycle_data):
            sorted_tracks[new_track_idx].extend(events)

    return sorted_tracks


def write_cycle_stats_csv(
    sorted_tracks: list[list[NoteEvent]],
    ticks_per_cycle: int,
    total_cycles: int,
    output_path: Path,
) -> None:
    """Write CSV with per-cycle statistics of weighted mean pitch across tracks."""
    num_tracks = len(sorted_tracks)

    track_cycles = [
        group_events_by_cycle(track, ticks_per_cycle, total_cycles)
        for track in sorted_tracks
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["cycle", "mean", "median", "max", "min", "std"])

        for cycle_idx in range(total_cycles):
            pitches = []
            for track_idx in range(num_tracks):
                events = track_cycles[track_idx][cycle_idx]
                mean_pitch = compute_duration_weighted_mean_pitch(events)
                if mean_pitch is not None:
                    pitches.append(mean_pitch)

            if len(pitches) == 0:
                writer.writerow([cycle_idx, "", "", "", "", ""])
            elif len(pitches) == 1:
                writer.writerow([
                    cycle_idx,
                    f"{pitches[0]:.2f}",
                    f"{pitches[0]:.2f}",
                    f"{pitches[0]:.2f}",
                    f"{pitches[0]:.2f}",
                    "0.00",
                ])
            else:
                writer.writerow([
                    cycle_idx,
                    f"{mean(pitches):.2f}",
                    f"{median(pitches):.2f}",
                    f"{max(pitches):.2f}",
                    f"{min(pitches):.2f}",
                    f"{stdev(pitches):.2f}",
                ])
