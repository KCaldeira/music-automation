"""Learned workflow, stage 2: generate melodies from a tables file.

Implements sections 4-7 of docs/learned_workflow.md:

    load + validate tables -> per track: one-bar rhythm walk, split into halves A/B
    -> lay out the form (e.g. "AB AB AA BC") -> per cycle: fill pitches backwards
    from the final tonic, using transitions x position-pitch weights.

Seeds: the rhythm stream is keyed by track m, the melody stream by cycle n, so
cycle n of every track uses the same melody random numbers.
"""

import json
from collections import Counter
from pathlib import Path

import numpy as np

from learner import TABLES_FORMAT

RHYTHM_STREAM, MELODY_STREAM = 1, 2   # SeedSequence spawn-key tags


# --------------------------------------------------------------------------
# Config and tables
# --------------------------------------------------------------------------

GEN_REQUIRED = ("tables", "output_dir", "form", "num_cycles", "num_tracks", "seed", "tempo")


def load_generation_config(path):
    cfg = json.loads(Path(path).read_text())

    def fail(msg):
        raise ValueError(f"Invalid generation config {path}: {msg}")

    missing = [k for k in GEN_REQUIRED if k not in cfg]
    if missing:
        fail(f"missing required keys: {missing}")
    cfg.setdefault("max_context", 3)
    cfg.setdefault("min_context_count", 3)
    cfg.setdefault("min_position_count", 3)
    # Each table's weights are raised to these powers before they are multiplied.
    # 0.5 (square roots) makes the product a geometric mean, so a preference that
    # both tables share (e.g. for the root) is not counted twice. 1 = plain product.
    cfg.setdefault("transition_exponent", 0.5)
    cfg.setdefault("position_exponent", 0.5)
    cfg.setdefault("velocity", 100)
    cfg.setdefault("track_name_prefix", Path(path).stem.split("_")[0])
    cfg.setdefault("write_mid", True)

    for k in ("num_cycles", "num_tracks", "max_context"):
        if isinstance(cfg[k], bool) or not isinstance(cfg[k], int) or cfg[k] < 1:
            fail(f"{k} must be a positive int (got {cfg[k]!r})")
    seed = cfg["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        fail(f"seed must be a non-negative int (got {seed!r})")
    for k in ("min_context_count", "min_position_count", "tempo",
              "transition_exponent", "position_exponent"):
        if not isinstance(cfg[k], (int, float)) or cfg[k] < 0:
            fail(f"{k} must be a non-negative number (got {cfg[k]!r})")
    if not 1 <= cfg["velocity"] <= 127:
        fail("velocity must be in 1..127")

    bars = cfg["form"].split()
    if not bars or any(len(b) != 2 or set(b) - set("ABC") for b in bars):
        fail(f"form must be bars of two letters from A, B, C, e.g. 'AB AB AA BC' (got {cfg['form']!r})")
    halves = "".join(bars)
    if "C" in halves[:-1]:
        fail("C (the ending half) may only be the last half of the form")
    cfg["form_bars"] = bars
    return cfg


def _weight_row(row, where):
    out = {}
    for key, w in row.items():
        if not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0:
            raise ValueError(f"{where}: weight for {key!r} must be a non-negative number (got {w!r})")
        out[int(key)] = float(w)
    return out


def load_tables(path):
    """Read and validate a tables file (section 3); returns plain Python structures."""
    doc = json.loads(Path(path).read_text())
    if doc.get("format") != TABLES_FORMAT:
        raise ValueError(f"{path}: format must be {TABLES_FORMAT!r} (got {doc.get('format')!r})")
    period = doc["period"]
    scale = doc["scale_semitones"]
    d = len(scale)

    p_start = doc["onsets"]["p_start"]
    if len(p_start) != period or any(not 0 <= p <= 1 for p in p_start):
        raise ValueError(f"{path}: onsets.p_start must be {period} values in 0..1")

    def per_position(name):
        table = {int(s): _weight_row(row, f"{path} {name}[{s}]") for s, row in doc[name].items()}
        bad = [s for s in table if not 0 <= s < period]
        if bad:
            raise ValueError(f"{path}: {name} has positions outside 0..{period - 1}: {bad}")
        return [table.get(s, {}) for s in range(period)]

    lengths = per_position("lengths")
    if any(L < 1 for row in lengths for L in row):
        raise ValueError(f"{path}: lengths must be at least 1 grid step")
    position_pitch = per_position("position_pitch")
    if any(not 0 <= c < d for row in position_pitch for c in row):
        raise ValueError(f"{path}: position_pitch classes must be 0..{d - 1}")

    transitions = {}
    for k, table in doc["transitions"].items():
        k = int(k)
        transitions[k] = {}
        for ctx, row in table.items():
            key = tuple(int(x) for x in ctx.split())
            if len(key) != k:
                raise ValueError(f"{path}: transitions[{k}] context {ctx!r} does not have {k} steps")
            transitions[k][key] = _weight_row(row, f"{path} transitions[{k}][{ctx!r}]")

    return {
        "source": str(path),
        "period": period,
        "scale": scale,
        "base_pitch": doc["base_pitch"],
        "root": doc["root"],
        "mode": doc.get("mode"),
        "chord": doc.get("chord"),
        "divisions_per_beat": doc["divisions_per_beat"],
        "beats_per_bar": doc["beats_per_bar"],
        "p_start": p_start,
        "lengths": lengths,
        "position_pitch": position_pitch,
        "transitions": transitions,
    }


def step_to_midi(step, tables):
    d = len(tables["scale"])
    octave, degree = divmod(step, d)
    return tables["base_pitch"] + 12 * octave + tables["scale"][degree]


# --------------------------------------------------------------------------
# Random streams (section 7)
# --------------------------------------------------------------------------

def rhythm_rng(seed, track):
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(RHYTHM_STREAM, track)))


def melody_rng(seed, cycle):
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(MELODY_STREAM, cycle)))


def _sample(rng, weights):
    """weights: {value: weight} with a positive total -> one value."""
    keys = sorted(weights)
    w = np.array([weights[k] for k in keys])
    return keys[int(rng.choice(len(keys), p=w / w.sum()))]


# --------------------------------------------------------------------------
# Rhythm and form (sections 4-5)
# --------------------------------------------------------------------------

def rhythm_walk(tables, rng, stats):
    """One bar of (start, length) notes, each cut at the half-bar line."""
    steps_per_bar = tables["divisions_per_beat"] * tables["beats_per_bar"]
    half = steps_per_bar // 2
    period = tables["period"]
    notes, s = [], 0
    while s < steps_per_bar:
        lengths = tables["lengths"][s % period]
        if rng.random() < tables["p_start"][s % period] and sum(lengths.values()) > 0:
            L = _sample(rng, lengths)
            end_of_half = half if s < half else steps_per_bar
            if s + L > end_of_half:
                stats["notes_cut_at_half_bar"] += 1
                L = end_of_half - s
            notes.append((s, L))
            s += L
        else:
            s += 1
    return notes


def cycle_slots(bar_notes, form_bars, tables):
    """Lay the halves out by the form. Returns [(cycle_position, length, is_tonic)]."""
    steps_per_bar = tables["divisions_per_beat"] * tables["beats_per_bar"]
    half = steps_per_bar // 2
    halves = {
        "A": [(s, L) for s, L in bar_notes if s < half],
        "B": [(s - half, L) for s, L in bar_notes if s >= half],
        "C": [(0, tables["divisions_per_beat"])],   # a quarter note, then rest
    }
    slots = []
    for b, bar in enumerate(form_bars):
        for h, letter in enumerate(bar):
            offset = b * steps_per_bar + h * half
            slots += [(offset + s, L, letter == "C") for s, L in halves[letter]]
    return slots, len(form_bars) * steps_per_bar


# --------------------------------------------------------------------------
# Backward melody fill (section 6)
# --------------------------------------------------------------------------

def fill_melody(slots, tables, cfg, rng, stats):
    """Pitches (scale steps) for every slot, chosen from the last slot backwards."""
    d = len(tables["scale"])
    period = tables["period"]
    T = tables["transitions"]
    max_k = min(cfg["max_context"], max(T, default=0))
    steps = [None] * len(slots)
    placed = []   # pitches after the current slot, in time order

    for j in range(len(slots) - 1, -1, -1):
        pos, _, is_tonic = slots[j]
        if is_tonic:
            steps[j] = 0
            placed.insert(0, 0)
            continue

        pos_row = tables["position_pitch"][pos % period]
        use_pos = sum(pos_row.values()) >= cfg["min_position_count"]
        stats["position_table_used" if use_pos else "position_table_thin"] += 1

        te, pe = cfg["transition_exponent"], cfg["position_exponent"]

        def pos_weight(p):
            return pos_row.get(p % d, 0.0) ** pe if use_pos else 1.0

        def flat(row):  # transition weights raised to te; zeros stay zero
            return {p: w ** te for p, w in row.items() if w > 0}

        # Orders whose context is well enough attested, longest first.
        qualifying = []
        for k in range(min(max_k, len(placed)), 0, -1):
            row = T.get(k, {}).get(tuple(placed[:k]), {})
            if sum(row.values()) >= cfg["min_context_count"]:
                qualifying.append((k, row))

        choice = None
        for k, row in qualifying:
            weights = {p: w * pos_weight(p) for p, w in flat(row).items()}
            if sum(weights.values()) > 0:
                stats[f"order_{k}"] += 1
                choice = _sample(rng, weights)
                break
        if choice is None and qualifying:
            k, row = qualifying[0]
            stats[f"order_{k}_without_position_table"] += 1
            choice = _sample(rng, flat(row))
        if choice is None:
            # Q2 (open): no well-attested context. Any k=1 evidence, else the overall
            # pitch frequency times the position table.
            row = T.get(1, {}).get(tuple(placed[:1]), {}) if placed else {}
            if sum(row.values()) > 0:
                stats["fallback_order_1_below_threshold"] += 1
                choice = _sample(rng, flat(row))
            else:
                overall = Counter()
                for r in T.get(1, {}).values():
                    overall.update(r)
                weights = {p: w * pos_weight(p) for p, w in flat(overall).items()}
                if sum(weights.values()) == 0:
                    weights = flat(overall)
                stats["fallback_overall_frequency"] += 1
                choice = _sample(rng, weights)

        steps[j] = choice
        placed.insert(0, choice)
    return steps


# --------------------------------------------------------------------------
# Whole run
# --------------------------------------------------------------------------

def generate(cfg, tables):
    """Returns (tracks, cycle_steps, stats); each track is a list of
    (start_step, length_steps, midi_pitch) over all cycles, plus its rhythm."""
    steps_per_bar = tables["divisions_per_beat"] * tables["beats_per_bar"]
    if steps_per_bar % 2:
        raise ValueError(f"a bar of {steps_per_bar} grid steps cannot be split into halves")
    stats = Counter()
    tracks = []
    for m in range(cfg["num_tracks"]):
        bar = rhythm_walk(tables, rhythm_rng(cfg["seed"], m), stats)
        slots, cycle_steps = cycle_slots(bar, cfg["form_bars"], tables)
        notes = []
        for n in range(cfg["num_cycles"]):
            pitches = fill_melody(slots, tables, cfg, melody_rng(cfg["seed"], n), stats)
            base = n * cycle_steps
            notes += [(base + pos, L, step_to_midi(x, tables))
                      for (pos, L, _), x in zip(slots, pitches)]
        tracks.append({"bar": bar, "notes": notes, "slots_per_cycle": len(slots)})
    return tracks, cycle_steps, stats
