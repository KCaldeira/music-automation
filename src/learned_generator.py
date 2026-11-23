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

import chords
from learner import TABLES_FORMAT

RHYTHM_STREAM, MELODY_STREAM, VELOCITY_STREAM = 1, 2, 3   # SeedSequence spawn-key tags


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
    # "tables": draw each velocity from the tables (by start position and length if that
    # cell has >= min_velocity_count notes, else by start position); or a fixed 1..127.
    cfg.setdefault("velocity", "tables")
    cfg.setdefault("min_velocity_count", 3)
    cfg.setdefault("track_name_prefix", Path(path).stem.split("_")[0])
    cfg.setdefault("write_mid", True)
    # Optional post-processing: one chord per bar of the form. Notes that were tones
    # of the tables' chord move to the nearest tone of that bar's chord.
    cfg.setdefault("chord_progression", None)
    cfg.setdefault("source_chord", None)   # default: the tables file's chord
    # The final note (the C half): "tonic" = base_pitch; "last_chord_root" = the root
    # of the progression's last chord, in the octave nearest base_pitch; "sampled" =
    # drawn like any note with no context (overall pitch frequency x position table).
    # Default: the last chord's root when there is a progression, else the tonic.
    cfg.setdefault("end_note", "last_chord_root" if cfg["chord_progression"] else "tonic")
    # Optional last filter: delete every note that STARTS in a window of one bar of the
    # form, e.g. [{"bar": 2, "from": 10, "to": 16}] = grid steps (16ths) 10-16 of bar 2,
    # both counted from 1 and inclusive. Notes held into the window are kept.
    cfg.setdefault("drop_starts", [])

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
    v = cfg["velocity"]
    if v != "tables" and (isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 127):
        fail(f"velocity must be 'tables' or an int in 1..127 (got {v!r})")

    # "walk": no half-bar form; the rhythm is walked over the whole cycle (the tables'
    # bars_per_cycle) and its last note is the end note.
    if cfg["form"] == "walk":
        cfg["form_bars"] = None
        bars = None
    else:
        bars = cfg["form"].split()
    if bars is not None and (not bars or any(len(b) != 2 or set(b) - set("ABC") for b in bars)):
        fail(f"form must be bars of two letters from A, B, C, e.g. 'AB AB AA BC' (got {cfg['form']!r})")
    if bars is not None:
        if "C" in "".join(bars)[:-1]:
            fail("C (the ending half) may only be the last half of the form")
        cfg["form_bars"] = bars

    prog = cfg["chord_progression"]
    if prog is not None:
        if isinstance(prog, str):
            prog = prog.split()
        if bars is not None and len(prog) != len(bars):
            fail(f"chord_progression has {len(prog)} chords but the form has {len(bars)} bars")
        try:
            for name in prog + ([cfg["source_chord"]] if cfg["source_chord"] else []):
                chords.parse_chord(name)
        except ValueError as e:
            fail(str(e))
        cfg["chord_progression"] = prog
    if cfg["end_note"] not in ("tonic", "last_chord_root", "sampled"):
        fail(f"end_note must be 'tonic', 'last_chord_root' or 'sampled' (got {cfg['end_note']!r})")
    if cfg["end_note"] == "last_chord_root" and not cfg["chord_progression"]:
        fail("end_note 'last_chord_root' needs a chord_progression")
    if not isinstance(cfg["drop_starts"], list):
        fail("drop_starts must be a list of {bar, from, to}")
    for w in cfg["drop_starts"]:
        ok = (isinstance(w, dict) and set(w) == {"bar", "from", "to"}
              and all(isinstance(w[k], int) and not isinstance(w[k], bool) for k in w)
              and 1 <= w["bar"] <= (len(bars) if bars else w["bar"]) and 1 <= w["from"] <= w["to"])
        if not ok:
            fail(f"drop_starts entry {w!r}: need {{'bar', 'from', 'to'}}, ints counted from 1, "
                 f"bar within the form, from <= to")
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

    velocity = velocity_by_length = None
    if "velocity" in doc:
        velocity = per_position("velocity")
        velocity_by_length = {}
        for key, row in doc.get("velocity_by_length", {}).items():
            s, L = (int(x) for x in key.split())
            velocity_by_length[(s, L)] = _weight_row(row, f"{path} velocity_by_length[{key!r}]")
        rows = velocity + list(velocity_by_length.values())
        if any(not 1 <= v <= 127 for row in rows for v in row):
            raise ValueError(f"{path}: velocities must be 1..127")

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
        "bars_per_cycle": doc["bars_per_cycle"],
        "p_start": p_start,
        "lengths": lengths,
        "position_pitch": position_pitch,
        "transitions": transitions,
        "velocity": velocity,
        "velocity_by_length": velocity_by_length,
    }


def end_step(cfg, tables):
    """Scale step of the final note: 0 (the tonic), or the last chord's root in the
    octave nearest base_pitch (the lower one if two are equally near), or None when
    it is sampled per cycle (end_note "sampled")."""
    if cfg["end_note"] == "tonic":
        return 0
    if cfg["end_note"] == "sampled":
        return None
    name = cfg["chord_progression"][-1]
    root_pc = next(pc for pc, role in chords.parse_chord(name).items() if role == "root")
    d = len(tables["scale"])
    options = [x for x in range(-d, d + 1) if step_to_midi(x, tables) % 12 == root_pc]
    if not options:
        raise ValueError(f"the root of {name} is not in the tables' scale, so the melody "
                         f"cannot end on it")
    return min(options, key=lambda x: (abs(step_to_midi(x, tables) - tables["base_pitch"]), x))


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


def velocity_rng(seed, track, cycle):
    """Keyed by track (like the rhythm) and by cycle, so velocities differ between
    tracks and change every 4-bar cycle. Separate from the rhythm and melody streams."""
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(VELOCITY_STREAM, track, cycle)))


def note_velocity(pos, length, tables, cfg, rng, stats):
    """Velocity for a note starting at cycle position pos (section 6c)."""
    if cfg["velocity"] != "tables":
        return cfg["velocity"]
    s = pos % tables["period"]
    cell = tables["velocity_by_length"].get((s, length), {})
    if sum(cell.values()) >= cfg["min_velocity_count"]:
        stats["velocity_by_position_and_length"] += 1
        return _sample(rng, cell)
    row = tables["velocity"][s]
    if sum(row.values()) >= cfg["min_velocity_count"]:
        stats["velocity_by_position"] += 1
        return _sample(rng, row)
    overall = Counter()
    for r in tables["velocity"]:
        overall.update(r)
    stats["velocity_overall"] += 1
    return _sample(rng, overall)


def _sample(rng, weights):
    """weights: {value: weight} with a positive total -> one value."""
    keys = sorted(weights)
    w = np.array([weights[k] for k in keys])
    return keys[int(rng.choice(len(keys), p=w / w.sum()))]


# --------------------------------------------------------------------------
# Rhythm and form (sections 4-5)
# --------------------------------------------------------------------------

def rhythm_walk(tables, rng, stats, total=None, cut_every=None, cut_name="half_bar"):
    """(start, length) notes over total grid steps (default one bar), each cut at the
    next multiple of cut_every (default the half-bar line)."""
    steps_per_bar = tables["divisions_per_beat"] * tables["beats_per_bar"]
    total = total or steps_per_bar
    cut_every = cut_every or steps_per_bar // 2
    period = tables["period"]
    notes, s = [], 0
    while s < total:
        lengths = tables["lengths"][s % period]
        if rng.random() < tables["p_start"][s % period] and sum(lengths.values()) > 0:
            L = _sample(rng, lengths)
            boundary = (s // cut_every + 1) * cut_every
            if s + L > boundary:
                stats[f"notes_cut_at_{cut_name}"] += 1
                L = boundary - s
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

def fill_melody(slots, tables, cfg, rng, stats, last=0):
    """Pitches (scale steps) for every slot, chosen from the last slot backwards.
    last: the fixed final note, or None to draw it like any note without context."""
    d = len(tables["scale"])
    period = tables["period"]
    T = tables["transitions"]
    max_k = min(cfg["max_context"], max(T, default=0))
    steps = [None] * len(slots)
    placed = []   # pitches after the current slot, in time order

    for j in range(len(slots) - 1, -1, -1):
        pos, _, is_tonic = slots[j]
        if is_tonic and last is not None:
            steps[j] = last
            placed.insert(0, last)
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
            # Q2: no well-attested context (or the sampled final note). Any k=1 evidence, else the overall
            # pitch frequency times the position table.
            row = T.get(1, {}).get(tuple(placed[:1]), {}) if placed else {}
            if is_tonic:
                row = {}
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
                stats["end_note_sampled" if is_tonic else "fallback_overall_frequency"] += 1
                choice = _sample(rng, weights)

        steps[j] = choice
        placed.insert(0, choice)
    return steps


# --------------------------------------------------------------------------
# Whole run
# --------------------------------------------------------------------------

def generate(cfg, tables):
    """Returns (tracks, cycle_steps, stats, moves); each track holds its rhythm and
    its notes, (start_step, length_steps, midi_pitch, velocity), over all cycles."""
    steps_per_bar = tables["divisions_per_beat"] * tables["beats_per_bar"]
    if steps_per_bar % 2:
        raise ValueError(f"a bar of {steps_per_bar} grid steps cannot be split into halves")
    if cfg["velocity"] == "tables" and tables["velocity"] is None:
        raise ValueError(f"{tables['source']} has no velocity tables (re-run the analysis), "
                         f"or set a fixed 'velocity' in the config")
    stats = Counter()
    last = end_step(cfg, tables)
    tracks = []
    for m in range(cfg["num_tracks"]):
        if cfg["form_bars"] is None:          # "walk": the whole cycle at once
            cycle_steps = tables["bars_per_cycle"] * steps_per_bar
            bar = rhythm_walk(tables, rhythm_rng(cfg["seed"], m), stats,
                              cycle_steps, cycle_steps, "cycle_end")
            slots = [(s, L, i == len(bar) - 1) for i, (s, L) in enumerate(bar)]
        else:
            bar = rhythm_walk(tables, rhythm_rng(cfg["seed"], m), stats)
            slots, cycle_steps = cycle_slots(bar, cfg["form_bars"], tables)
        notes = []
        for n in range(cfg["num_cycles"]):
            pitches = fill_melody(slots, tables, cfg, melody_rng(cfg["seed"], n), stats, last)
            base = n * cycle_steps
            vrng = velocity_rng(cfg["seed"], m, n)
            velocities = [note_velocity(pos, L, tables, cfg, vrng, stats) for pos, L, _ in slots]
            notes += [(base + pos, L, step_to_midi(x, tables), v)
                      for (pos, L, _), x, v in zip(slots, pitches, velocities)]
        tracks.append({"bar": bar, "notes": notes, "slots_per_cycle": len(slots)})

    moves = Counter()
    n_bars = cycle_steps // steps_per_bar
    if cfg["chord_progression"] and len(cfg["chord_progression"]) != n_bars:
        raise ValueError(f"chord_progression has {len(cfg['chord_progression'])} chords but a "
                         f"cycle has {n_bars} bars")
    if any(w["bar"] > n_bars for w in cfg["drop_starts"]):
        raise ValueError(f"drop_starts names a bar past the cycle's {n_bars} bars")
    if cfg["chord_progression"]:
        source_name = cfg["source_chord"] or tables["chord"]
        for t in tracks:
            t["notes"] = follow_progression(t["notes"], cfg["chord_progression"], source_name,
                                            steps_per_bar, cycle_steps, moves)
    if cfg["drop_starts"]:
        for w in cfg["drop_starts"]:
            if w["to"] > steps_per_bar:
                raise ValueError(f"drop_starts {w!r}: a bar has only {steps_per_bar} grid steps")
        drop = {(w["bar"] - 1) * steps_per_bar + s - 1
                for w in cfg["drop_starts"] for s in range(w["from"], w["to"] + 1)}
        for t in tracks:
            kept = [x for x in t["notes"] if x[0] % cycle_steps not in drop]
            stats["dropped by drop_starts"] += len(t["notes"]) - len(kept)
            t["notes"] = kept
    return tracks, cycle_steps, stats, moves


def follow_progression(notes, progression, source_name, steps_per_bar, cycle_steps, moves):
    """Post-processing: in bar b of each cycle, notes that were tones of the source
    chord move to the nearest tone of progression[b]; passing tones stay put.
    moves counts (bar chord, old pitch class, new pitch class) for the report."""
    source = chords.parse_chord(source_name)
    targets = [chords.parse_chord(c) for c in progression]
    out = []
    for start, length, pitch, velocity in notes:
        b = (start % cycle_steps) // steps_per_bar
        new = chords.retarget_pitch(pitch, source, targets[b])
        if new != pitch:
            moves[(progression[b], pitch % 12, new % 12)] += 1
        out.append((start, length, new, velocity))
    return out
