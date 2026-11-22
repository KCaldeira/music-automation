"""Learned workflow, stage 1: analyse a MIDI melody into probability tables.

Implements sections 2 and 3 of docs/learned_workflow.md:

    read notes -> clean (quantize, drop zero-length, same-start, overlaps)
               -> pitches to scale steps (snapping chromatic notes)
               -> tables T1 (onsets), T2 (lengths), T3 (position pitch),
                  T4 (backward transitions)
               -> a hand-editable JSON tables file

The tables store weights (counts), except p_start, which is a probability.
"""

import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import mido


TABLES_FORMAT = "music-automation learned tables v1"

MODES = {
    "ionian":     [0, 2, 4, 5, 7, 9, 11],
    "major":      [0, 2, 4, 5, 7, 9, 11],
    "dorian":     [0, 2, 3, 5, 7, 9, 10],
    "phrygian":   [0, 1, 3, 5, 7, 8, 10],
    "lydian":     [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "aeolian":    [0, 2, 3, 5, 7, 8, 10],
    "minor":      [0, 2, 3, 5, 7, 8, 10],
    "locrian":    [0, 1, 3, 5, 6, 8, 10],
}

PITCH_CLASSES = {
    "C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4,
    "E#": 5, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

REQUIRED_KEYS = (
    "input_midi", "output_path", "root", "chord", "base_pitch",
    "divisions_per_beat", "beats_per_bar", "bars_per_cycle", "input_quantization",
)


def load_analysis_config(path):
    cfg = json.loads(Path(path).read_text())

    def fail(msg):
        raise ValueError(f"Invalid analysis config {path}: {msg}")

    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        fail(f"missing required keys: {missing}")

    cfg.setdefault("mode", None)
    cfg.setdefault("scale_semitones", None)
    cfg.setdefault("period", cfg["divisions_per_beat"] * cfg["beats_per_bar"])
    cfg.setdefault("snap_priority", [0, 2, 4, 5])
    cfg.setdefault("max_context", 3)

    for k in ("divisions_per_beat", "beats_per_bar", "bars_per_cycle", "period", "max_context"):
        if isinstance(cfg[k], bool) or not isinstance(cfg[k], int) or cfg[k] < 1:
            fail(f"{k} must be a positive int (got {cfg[k]!r})")

    # Scale: a mode name or an explicit list (if both are given they must agree).
    if cfg["mode"] is None and cfg["scale_semitones"] is None:
        fail("give 'mode' or 'scale_semitones'")
    if cfg["mode"] is not None:
        if cfg["mode"].lower() not in MODES:
            fail(f"unknown mode {cfg['mode']!r}; known: {sorted(MODES)}")
        from_mode = MODES[cfg["mode"].lower()]
        if cfg["scale_semitones"] is not None and cfg["scale_semitones"] != from_mode:
            fail(f"scale_semitones {cfg['scale_semitones']} disagrees with mode {cfg['mode']!r}")
        cfg["scale_semitones"] = from_mode
    scale = cfg["scale_semitones"]
    if scale[0] != 0 or sorted(set(scale)) != scale or scale[-1] > 11:
        fail(f"scale_semitones must start at 0 and rise strictly within 0..11 (got {scale})")

    if cfg["root"] not in PITCH_CLASSES:
        fail(f"unknown root {cfg['root']!r}")
    base = cfg["base_pitch"]
    if isinstance(base, bool) or not isinstance(base, int) or not 0 <= base <= 127:
        fail(f"base_pitch must be an int in 0..127 (got {base!r})")
    if base % 12 != PITCH_CLASSES[cfg["root"]]:
        fail(f"base_pitch {base} is not a {cfg['root']} (root)")

    d = len(scale)
    if any(not isinstance(c, int) or not 0 <= c < d for c in cfg["snap_priority"]):
        fail(f"snap_priority entries must be degree classes 0..{d - 1}")

    steps_per_bar = cfg["divisions_per_beat"] * cfg["beats_per_bar"]
    if cfg["period"] % steps_per_bar and steps_per_bar % cfg["period"]:
        fail(f"period ({cfg['period']}) must divide or be a multiple of a bar ({steps_per_bar})")

    # input_quantization "1/N": N notes per whole note. It must be a whole number of grid steps.
    q = str(cfg["input_quantization"])
    if not q.startswith("1/") or not q[2:].isdigit() or int(q[2:]) < 1:
        fail(f"input_quantization must look like '1/16' (got {q!r})")
    grid_per_whole = 4 * cfg["divisions_per_beat"]  # grid steps per whole note (beat = quarter)
    if grid_per_whole % int(q[2:]):
        fail(f"input_quantization {q} is not a whole number of grid steps "
             f"(grid is 1/{grid_per_whole})")
    cfg["quant_steps"] = grid_per_whole // int(q[2:])
    return cfg


# --------------------------------------------------------------------------
# Reading and cleaning (section 2)
# --------------------------------------------------------------------------

def read_midi_notes(path):
    """All notes in the file as (start_tick, end_tick, pitch), plus file info."""
    mid = mido.MidiFile(path)
    notes, unmatched_offs, hanging = [], 0, 0
    info = {"ticks_per_beat": mid.ticks_per_beat, "tempo_bpm": None, "time_signature": None}
    for track in mid.tracks:
        tick, sounding = 0, defaultdict(list)
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo" and info["tempo_bpm"] is None:
                info["tempo_bpm"] = round(mido.tempo2bpm(msg.tempo), 3)
            elif msg.type == "time_signature" and info["time_signature"] is None:
                info["time_signature"] = f"{msg.numerator}/{msg.denominator}"
            elif msg.type == "note_on" and msg.velocity > 0:
                sounding[msg.note].append(tick)
            elif msg.type in ("note_off", "note_on"):
                if sounding[msg.note]:
                    notes.append((sounding[msg.note].pop(0), tick, msg.note))
                else:
                    unmatched_offs += 1
        hanging += sum(len(v) for v in sounding.values())
    info["unmatched_note_offs"] = unmatched_offs
    info["notes_never_ended"] = hanging
    return notes, info


def clean_notes(raw_notes, ticks_per_step, quant_steps):
    """Section 2 cleaning. Returns notes as (start, end, pitch) in grid steps, and a report."""
    q_ticks = ticks_per_step * quant_steps

    def snap(tick):  # nearest quantization line, halves round up
        return ((2 * tick + q_ticks) // (2 * q_ticks)) * quant_steps

    report = {"raw_notes": len(raw_notes)}
    snapped = [(snap(s), snap(e), p) for s, e, p in raw_notes]
    report["moved_by_quantizing"] = sum(
        1 for (s, e, _) in raw_notes if s % q_ticks or e % q_ticks)

    # 1-2. quantize, then ignore zero-length notes
    notes = [n for n in snapped if n[1] > n[0]]
    report["dropped_zero_length"] = len(snapped) - len(notes)

    # 3. same-start notes: keep the longest, ties to the higher pitch
    by_start = defaultdict(list)
    for n in notes:
        by_start[n[0]].append(n)
    notes = [max(group, key=lambda n: (n[1] - n[0], n[2])) for group in by_start.values()]
    report["dropped_same_start"] = sum(len(g) - 1 for g in by_start.values())

    # 4. overlaps: the earlier note ends where the next one starts
    notes.sort()
    cut = 0
    for i in range(len(notes) - 1):
        s, e, p = notes[i]
        if e > notes[i + 1][0]:
            notes[i] = (s, notes[i + 1][0], p)
            cut += 1
    report["cut_at_next_start"] = cut
    report["notes_after_cleaning"] = len(notes)
    return notes, report


# --------------------------------------------------------------------------
# Pitch to scale step (section 2, items 4-5)
# --------------------------------------------------------------------------

def pitch_to_step(pitch, base_pitch, scale, snap_priority):
    """Return (scale step, was_snapped). Chromatic notes go to the neighbour whose
    degree class comes first in snap_priority; otherwise to the lower neighbour."""
    d = len(scale)
    octave, semis = divmod(pitch - base_pitch, 12)
    if semis in scale:
        return octave * d + scale.index(semis), False
    below = max(i for i, s in enumerate(scale) if s < semis)  # scale[0] = 0 < semis
    lower = octave * d + below
    upper = lower + 1
    rank = {c: i for i, c in enumerate(snap_priority)}
    lower_rank = rank.get(lower % d, len(rank))
    upper_rank = rank.get(upper % d, len(rank))
    return (upper if upper_rank < lower_rank else lower), True


# --------------------------------------------------------------------------
# Tables (section 3)
# --------------------------------------------------------------------------

def build_tables(notes, steps, cfg):
    """notes: cleaned (start, end, pitch) in grid steps; steps: scale step of each note."""
    period = cfg["period"]
    d = len(cfg["scale_semitones"])
    steps_per_bar = cfg["divisions_per_beat"] * cfg["beats_per_bar"]

    # T1: walk the timeline to the end of the last bar that holds a note.
    last_end = max(e for _, e, _ in notes)
    timeline = -(-last_end // steps_per_bar) * steps_per_bar
    held = set()
    for s, e, _ in notes:
        held.update(range(s + 1, e))
    starts = {s for s, _, _ in notes}
    n_free, n_start = [0] * period, [0] * period
    for t in range(timeline):
        if t not in held:
            n_free[t % period] += 1
            n_start[t % period] += t in starts
    p_start = [round(n_start[i] / n_free[i], 4) if n_free[i] else 0 for i in range(period)]

    # T2 and T3, per position
    lengths = {i: Counter() for i in range(period)}
    position_pitch = {i: Counter({c: 0 for c in range(d)}) for i in range(period)}
    for (s, e, _), x in zip(notes, steps):
        lengths[s % period][e - s] += 1
        position_pitch[s % period][x % d] += 1

    # T4: backward transitions, context = the k notes that follow
    transitions = {}
    for k in range(1, cfg["max_context"] + 1):
        table = defaultdict(Counter)
        for i in range(len(steps) - k):
            table[tuple(steps[i + 1:i + 1 + k])][steps[i]] += 1
        transitions[k] = table

    return {
        "onsets": {"p_start": p_start, "n_start": n_start, "n_free": n_free},
        "lengths": lengths,
        "position_pitch": position_pitch,
        "transitions": transitions,
        "timeline_steps": timeline,
    }


def analyze(cfg):
    """Run the whole analysis. Returns (tables document as a dict, report dict)."""
    raw, info = read_midi_notes(cfg["input_midi"])
    if not raw:
        raise ValueError(f"No notes in {cfg['input_midi']}")
    if info["ticks_per_beat"] % cfg["divisions_per_beat"]:
        raise ValueError(f"MIDI resolution {info['ticks_per_beat']} ticks per beat is not "
                         f"divisible by divisions_per_beat {cfg['divisions_per_beat']}")
    ticks_per_step = info["ticks_per_beat"] // cfg["divisions_per_beat"]
    notes, report = clean_notes(raw, ticks_per_step, cfg["quant_steps"])

    scale, d = cfg["scale_semitones"], len(cfg["scale_semitones"])
    converted = [pitch_to_step(p, cfg["base_pitch"], scale, cfg["snap_priority"])
                 for _, _, p in notes]
    steps = [x for x, _ in converted]
    report["snapped_to_key"] = sum(1 for _, was in converted if was)
    report["midi_range"] = [min(p for *_, p in notes), max(p for *_, p in notes)]
    report["step_range"] = [min(steps), max(steps)]
    report["degree_class_totals"] = {c: sum(1 for x in steps if x % d == c) for c in range(d)}
    report["source"] = info

    tables = build_tables(notes, steps, cfg)
    steps_per_bar = cfg["divisions_per_beat"] * cfg["beats_per_bar"]
    report["bars"] = tables["timeline_steps"] // steps_per_bar

    doc = {
        "format": TABLES_FORMAT,
        "created": date.today().isoformat(),
        "source_midi": cfg["input_midi"],
        "source_tempo_bpm": info["tempo_bpm"],
        "source_time_signature": info["time_signature"],
        "root": cfg["root"],
        "mode": cfg["mode"],
        "scale_semitones": scale,
        "chord": cfg["chord"],
        "base_pitch": cfg["base_pitch"],
        "divisions_per_beat": cfg["divisions_per_beat"],
        "beats_per_bar": cfg["beats_per_bar"],
        "bars_per_cycle": cfg["bars_per_cycle"],
        "input_quantization": cfg["input_quantization"],
        "period": cfg["period"],
        "snap_priority": cfg["snap_priority"],
        "max_context": cfg["max_context"],
        "cleaning": {k: report[k] for k in (
            "raw_notes", "moved_by_quantizing", "dropped_zero_length",
            "dropped_same_start", "cut_at_next_start", "notes_after_cleaning",
            "snapped_to_key")},
        **{k: tables[k] for k in ("onsets", "lengths", "position_pitch", "transitions")},
    }
    return doc, report


# --------------------------------------------------------------------------
# Writing the tables file: valid JSON, one entry per line, for hand editing
# --------------------------------------------------------------------------

def _inline(obj):
    return json.dumps(obj, separators=(", ", ": "))


def _weights(counter):
    """{"2": 5, "4": 1}, keys in numeric order."""
    return "{" + ", ".join(f'"{k}": {v}' for k, v in sorted(counter.items())) + "}"


def _rows(rows, indent):
    """rows: list of (key string, inline value string) -> aligned JSON lines."""
    pad = " " * indent
    width = max((len(k) for k, _ in rows), default=0) + 3
    lines = [f'{pad}{(chr(34) + k + chr(34) + ":").ljust(width)} {v}' for k, v in rows]
    return ",\n".join(lines)


def format_tables(doc):
    out = ["{"]
    header = [k for k in doc if k not in ("cleaning", "onsets", "lengths",
                                          "position_pitch", "transitions")]
    out.append(",\n".join(f'  "{k}": {_inline(doc[k])}' for k in header) + ",")
    out.append("")
    out.append(f'  "cleaning": {_inline(doc["cleaning"])},')
    out.append("")

    on = doc["onsets"]
    out.append('  "onsets": {')
    out.append(_rows([(k, _inline(on[k])) for k in ("p_start", "n_start", "n_free")], 4))
    out.append("  },")
    out.append("")

    for name in ("lengths", "position_pitch"):
        out.append(f'  "{name}": {{')
        out.append(_rows([(str(s), _weights(c)) for s, c in sorted(doc[name].items())], 4))
        out.append("  },")
        out.append("")

    out.append('  "transitions": {')
    blocks = []
    for k, table in sorted(doc["transitions"].items()):
        rows = [(" ".join(map(str, ctx)), _weights(c)) for ctx, c in sorted(table.items())]
        blocks.append(f'    "{k}": {{\n' + _rows(rows, 6) + "\n    }")
    out.append(",\n".join(blocks))
    out.append("  }")
    out.append("}")
    text = "\n".join(out) + "\n"
    json.loads(text)  # the hand-editable layout must still be valid JSON
    return text
