"""Learned workflow, elaboration method (docs/learned_workflow.md, section 6f).

The June elaboration process (src/elaborator.py), driven by a learned tables file
instead of hand-set weights. Each track starts from one held tonic; each later
cycle is the previous one plus `changes_per_cycle` edits: split a note, join it
over a rest, start a new note, re-pitch a note, or turn a note into a rest. The
changes accumulate, and by default the cycle order is reversed at output so the
track runs from busy to the single held note.

The per-division probabilities come from the tables' onset probability p_start,
mirroring how the old hand-set configs derived them from one weight c per
division (start = c/6, rest = (1 - start)^2, change proportional to c):

    start[d]  = p_start[s]
    change[d] = p_start[s] ** change_exponent     (where to edit; a weight)
    rest[d]   = (1 - p_start[s]) ** rest_exponent

with s = d mod period. Pitches are scale steps, chosen from the transition tables
using both the note before and the notes after (choose_pitch), times the position
pitch table.
"""

import copy
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import elaborator as el
from elaborator import START, SUSTAIN, REST
import learned_generator as lg

ELABORATE_STREAM = 7    # SeedSequence spawn-key tag: (7, track)

REQUIRED = ("tables", "output_dir", "num_cycles", "num_tracks", "seed", "tempo")


def load_config(path):
    cfg = json.loads(Path(path).read_text())

    def fail(msg):
        raise ValueError(f"Invalid elaboration config {path}: {msg}")

    if cfg.get("method") != "elaborate":
        fail("method must be 'elaborate'")
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        fail(f"missing required keys: {missing}")
    defaults = {
        "changes_per_cycle": 1,
        # true: busy -> one held note (the old default); false: the other way;
        # "both": each track written twice, the reversed copy with a "C" suffix.
        "reverse_cycle_order": True,
        # {"bar", "step", "length"} (counted from 1): a fixed, frozen last note.
        "final_note": None,
        # "input_states": at the picked division, draw a target state (note start,
        # held, silent) from the input's frequencies there and make the edit that
        # gets there (split / new note / re-pitch, join / extend, cut to rest).
        # "old": the June rules (elaborator.classify_and_apply) with p_start.
        "edit_rules": "input_states",
        # Where edits land: "uniform"; "starts" = in proportion to how often a note
        # starts at that position in the input (n_start); "p_start" = the onset
        # probability given a free line (the first mapping of the old weights).
        "change_weight": "uniform",
        "change_exponent": 1.0,
        "rest_exponent": 2.0,
        # "lengths": a new note's length is drawn from the tables' lengths;
        # "extend": the old walk over rests with chance 1 - p_start per division.
        "new_note_length": "lengths",
        "max_context": 3,
        "min_context_count": 3,
        "min_position_count": 3,
        "transition_exponent": 0.5,
        "position_exponent": 0.5,
        "velocity": "tables",
        "min_velocity_count": 3,
        "track_name_prefix": Path(path).stem.split("_")[0],
        "write_mid": True,
    }
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    known = set(REQUIRED) | set(defaults) | {"method", "description"}
    unknown = sorted(set(cfg) - known)
    if unknown:
        fail(f"unknown keys: {unknown}")

    for k in ("num_cycles", "num_tracks", "max_context", "changes_per_cycle"):
        if isinstance(cfg[k], bool) or not isinstance(cfg[k], int) or cfg[k] < 1:
            fail(f"{k} must be a positive int (got {cfg[k]!r})")
    if isinstance(cfg["seed"], bool) or not isinstance(cfg["seed"], int) or cfg["seed"] < 0:
        fail(f"seed must be a non-negative int (got {cfg['seed']!r})")
    for k in ("min_context_count", "min_position_count", "tempo", "transition_exponent",
              "position_exponent", "change_exponent", "rest_exponent"):
        if isinstance(cfg[k], bool) or not isinstance(cfg[k], (int, float)) or cfg[k] < 0:
            fail(f"{k} must be a non-negative number (got {cfg[k]!r})")
    if cfg["reverse_cycle_order"] not in (True, False, "both"):
        fail(f"reverse_cycle_order must be true, false or 'both' (got {cfg['reverse_cycle_order']!r})")
    if cfg["edit_rules"] not in ("input_states", "old"):
        fail(f"edit_rules must be 'input_states' or 'old' (got {cfg['edit_rules']!r})")
    if cfg["change_weight"] not in ("uniform", "starts", "p_start"):
        fail(f"change_weight must be 'uniform', 'starts' or 'p_start' (got {cfg['change_weight']!r})")
    if cfg["new_note_length"] not in ("lengths", "extend"):
        fail(f"new_note_length must be 'lengths' or 'extend' (got {cfg['new_note_length']!r})")
    v = cfg["velocity"]
    if v != "tables" and (isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 127):
        fail(f"velocity must be 'tables' or an int in 1..127 (got {v!r})")
    fn = cfg["final_note"]
    if fn is not None and (not isinstance(fn, dict) or set(fn) != {"bar", "step", "length"}
                           or any(isinstance(x, bool) or not isinstance(x, int) or x < 1
                                  for x in fn.values())):
        fail(f"final_note must be {{bar, step, length}}, positive ints counted from 1 (got {fn!r})")
    return cfg


# --------------------------------------------------------------------------
# Probabilities and pitch choice
# --------------------------------------------------------------------------

def division_probs(tables, cycle_steps, cfg, frozen):
    """(change, start, rest) vectors over the cycle's divisions."""
    period = tables["period"]
    p = [tables["p_start"][d % period] for d in range(cycle_steps)]
    if cfg["change_weight"] == "starts":
        if not tables.get("n_start"):
            raise ValueError(f"{tables['source']} has no onsets.n_start; use change_weight 'p_start'")
        base = [float(tables["n_start"][d % period]) for d in range(cycle_steps)]
    elif cfg["change_weight"] == "uniform":
        base = [1.0] * cycle_steps
    else:
        base = p
    change = [0.0 if frozen[d] else x ** cfg["change_exponent"] for d, x in enumerate(base)]
    rest = [(1.0 - x) ** cfg["rest_exponent"] for x in p]
    return change, p, rest


def two_sided_index(tables):
    """{k: {(tail, prev): {p: weight}}}: the count of prev -> p -> tail, read from
    transitions[k][(p,) + tail][prev]."""
    idx = {}
    for k, table in tables["transitions"].items():
        rows = defaultdict(dict)
        for ctx, row in table.items():
            p, tail = ctx[0], ctx[1:]
            for prev, w in row.items():
                if w > 0:
                    rows[(tail, prev)][p] = w
        idx[k] = dict(rows)
    return idx


def neighbours(grid, d, max_k):
    """Pitch of the nearest note starting before d (None if none), and the pitches
    of up to max_k notes starting after d, in time order."""
    prev = None
    for j in range(d - 1, -1, -1):
        if grid.kind[j] == START:
            prev = grid.pitch[j]
            break
    following = []
    for j in range(d + 1, len(grid.kind)):
        if grid.kind[j] == START:
            following.append(grid.pitch[j])
            if len(following) == max_k:
                break
    return prev, following


def choose_pitch(grid, d, tables, cfg, rng, stats, ctx, exclude=None):
    """A scale step for a note at division d, or None if nothing is allowed (only
    possible with exclude, for a re-pitch). Contexts are tried in this order, each
    used if its total weight is >= min_context_count:
      two-sided  prev -> p -> f1..f(k-1)    k = K..2
      backward   p -> f1..fk                k = K..1
      forward    prev -> p                  (two-sided with k = 1)
    then the overall pitch frequency. Each is multiplied by the position table."""
    T, idx = tables["transitions"], ctx["two_sided"]
    K = min(cfg["max_context"], max(T, default=0))
    prev, following = neighbours(grid, d, K)
    d_cls = len(tables["scale"])
    pos_row = tables["position_pitch"][d % tables["period"]]
    use_pos = sum(pos_row.values()) >= cfg["min_position_count"]
    te, pe = cfg["transition_exponent"], cfg["position_exponent"]

    candidates = []      # (label, row) in order of preference
    if prev is not None:
        for k in range(min(K, len(following) + 1), 1, -1):
            candidates.append((f"two_sided_k{k}", idx.get(k, {}).get((tuple(following[:k - 1]), prev), {})))
    for k in range(min(K, len(following)), 0, -1):
        candidates.append((f"backward_k{k}", T.get(k, {}).get(tuple(following[:k]), {})))
    if prev is not None:
        candidates.append(("forward", idx.get(1, {}).get(((), prev), {})))
    qualifying = [(lab, row) for lab, row in candidates if sum(row.values()) >= cfg["min_context_count"]]

    def weigh(row, with_pos):
        out = {}
        for p, w in row.items():
            if w <= 0 or p == exclude:
                continue
            x = w ** te
            if with_pos and use_pos:
                x *= pos_row.get(p % d_cls, 0.0) ** pe
            if x > 0:
                out[p] = x
        return out

    for lab, row in qualifying:
        w = weigh(row, True)
        if w:
            stats[f"pitch_{lab}"] += 1
            return lg._sample(rng, w)
    for lab, row in qualifying[:1]:
        w = weigh(row, False)
        if w:
            stats[f"pitch_{lab}_without_position_table"] += 1
            return lg._sample(rng, w)
    overall = ctx["overall"]
    w = weigh(overall, True) or weigh(overall, False)
    if w:
        stats["pitch_overall_frequency"] += 1
        return lg._sample(rng, w)
    return None


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------

def forward_extension(grid, start_d, start_prob, frozen, rng):
    """Extend the note starting at start_d over the rests after it: each rest j is
    absorbed with chance 1 - start_prob[j]; stop at a note, a frozen division, a
    failed draw, or the cycle end."""
    n = len(grid.kind)
    pitch = grid.pitch[start_d]
    j = el.note_end(grid, start_d) + 1
    while j < n and grid.kind[j] == REST and not frozen[j]:
        if rng.random() < 1.0 - start_prob[j]:
            grid.kind[j], grid.pitch[j] = SUSTAIN, pitch
            j += 1
        else:
            break


def apply_edit(grid, d, probs, frozen, tables, cfg, rng, stats, ctx):
    """One edit at division d (the old cases a / b / c)."""
    _, start, rest = probs
    k = grid.kind[d]
    if k == START:                                   # (a) a note start
        if rng.random() < rest[d]:
            el._make_rest_run(grid, d)
            stats["edit_note_to_rest"] += 1
        else:
            new = choose_pitch(grid, d, tables, cfg, rng, stats, ctx, exclude=grid.pitch[d])
            if new is None:
                stats["edit_repitch_impossible"] += 1
                return
            for j in range(d, el.note_end(grid, d) + 1):
                grid.pitch[j] = new
            stats["edit_repitch"] += 1
    elif k == REST:                                  # (b) a rest
        if el.is_sounding(grid, d - 1) and rng.random() < 1.0 - start[d]:
            ps = el.note_start_index(grid, d - 1)    # join: extend the note before
            grid.kind[d], grid.pitch[d] = SUSTAIN, grid.pitch[ps]
            forward_extension(grid, ps, start, frozen, rng)
            stats["edit_join"] += 1
        else:                                        # a new note
            pitch = choose_pitch(grid, d, tables, cfg, rng, stats, ctx)
            grid.kind[d], grid.pitch[d] = START, pitch
            if cfg["new_note_length"] == "extend":
                forward_extension(grid, d, start, frozen, rng)
            else:
                lengths = tables["lengths"][d % tables["period"]]
                L = lg._sample(rng, lengths) if sum(lengths.values()) > 0 else 1
                j = d + 1
                while j < d + L and j < len(grid.kind) and grid.kind[j] == REST and not frozen[j]:
                    grid.kind[j], grid.pitch[j] = SUSTAIN, pitch
                    j += 1
                if j < d + L:
                    stats["new_note_cut_short"] += 1
            stats["edit_new_note"] += 1
    else:                                            # (c) mid-note: split
        if rng.random() < rest[d]:
            el._make_rest_run(grid, d)
            stats["edit_split_to_rest"] += 1
        else:
            grid.kind[d] = START
            stats["edit_split_resound"] += 1


def state_probs(tables, cycle_steps):
    """Per division: the input's (start, held, silent) frequencies at its position."""
    if not tables.get("n_held"):
        raise ValueError(f"{tables['source']} has no onsets.n_held (re-run the analysis), "
                         f"or use edit_rules 'old'")
    out = []
    for d in range(cycle_steps):
        s = d % tables["period"]
        st, fr, hd = tables["n_start"][s], tables["n_free"][s], tables["n_held"][s]
        tot = fr + hd
        out.append((st / tot, hd / tot, (fr - st) / tot) if tot else (0.0, 0.0, 1.0))
    return out


def new_note(grid, d, frozen, tables, cfg, rng, stats, ctx):
    """Start a note at rest division d: pitch drawn, length from the lengths table,
    cut at the next note or frozen division."""
    pitch = choose_pitch(grid, d, tables, cfg, rng, stats, ctx)
    grid.kind[d], grid.pitch[d] = START, pitch
    lengths = tables["lengths"][d % tables["period"]]
    L = lg._sample(rng, lengths) if sum(lengths.values()) > 0 else 1
    j = d + 1
    while j < d + L and j < len(grid.kind) and grid.kind[j] == REST and not frozen[j]:
        grid.kind[j], grid.pitch[j] = SUSTAIN, pitch
        j += 1
    if j < d + L:
        stats["new_note_cut_short"] += 1


def apply_state_edit(grid, d, target_probs, frozen, tables, cfg, rng, stats, ctx):
    """Draw a target state for division d from the input's frequencies and make the
    edit that reaches it. Returns True if the grid changed."""
    p_st, p_hd, p_si = target_probs[d]
    u = rng.random() * (p_st + p_hd + p_si)
    target = START if u < p_st else SUSTAIN if u < p_st + p_hd else REST
    k = grid.kind[d]
    if target == START:
        if k == START:                               # re-pitch
            new = choose_pitch(grid, d, tables, cfg, rng, stats, ctx, exclude=grid.pitch[d])
            if new is None:
                return False
            for j in range(d, el.note_end(grid, d) + 1):
                grid.pitch[j] = new
            stats["edit_repitch"] += 1
        elif k == SUSTAIN:                           # split: re-sound the same pitch
            grid.kind[d] = START
            stats["edit_split"] += 1
        else:                                        # new note in a rest
            new_note(grid, d, frozen, tables, cfg, rng, stats, ctx)
            stats["edit_new_note"] += 1
        return True
    if target == SUSTAIN:
        if not el.is_sounding(grid, d - 1) or k == SUSTAIN:
            return False
        pitch = grid.pitch[el.note_start_index(grid, d - 1)]
        if k == START:                               # join into the note before
            for j in range(d, el.note_end(grid, d) + 1):
                grid.pitch[j] = pitch
            grid.kind[d] = SUSTAIN
            stats["edit_join"] += 1
        else:                                        # extend the note before over d
            grid.kind[d], grid.pitch[d] = SUSTAIN, pitch
            stats["edit_extend"] += 1
        return True
    if k == REST:                                    # target silent
        return False
    stats["edit_note_to_rest" if k == START else "edit_cut_to_rest"] += 1
    el._make_rest_run(grid, d)
    return True


# --------------------------------------------------------------------------
# Whole run
# --------------------------------------------------------------------------

def initial_grid(cycle_steps, final):
    """Cycle 0: the tonic (step 0) held over the cycle, or up to the final note,
    which is then the tonic again; the divisions from the final note on are frozen."""
    grid = el.init_cycle(cycle_steps, 0)
    frozen = [False] * cycle_steps
    if final is not None:
        fs, fl = final
        grid.kind[fs] = START
        for j in range(fs + fl, cycle_steps):
            grid.kind[j], grid.pitch[j] = REST, None
        for j in range(fs, cycle_steps):
            frozen[j] = True
    return grid, frozen


def generate(cfg, tables):
    """Returns (tracks, cycle_steps, stats). Each track: {"name_suffix", "notes":
    [(start_step, length_steps, midi_pitch, velocity)], "counts": notes per output
    cycle, "busiest": [(start, length)] of its busiest cycle}."""
    steps_per_bar = tables["divisions_per_beat"] * tables["beats_per_bar"]
    cycle_steps = tables["bars_per_cycle"] * steps_per_bar
    if cfg["velocity"] == "tables" and tables["velocity"] is None:
        raise ValueError(f"{tables['source']} has no velocity tables (re-run the analysis)")
    final = None
    if cfg["final_note"] is not None:
        fn = cfg["final_note"]
        fs = (fn["bar"] - 1) * steps_per_bar + fn["step"] - 1
        if fn["step"] > steps_per_bar or fs + fn["length"] > cycle_steps:
            raise ValueError(f"final_note {fn!r} does not fit in a cycle of "
                             f"{tables['bars_per_cycle']} bars of {steps_per_bar} grid steps")
        final = (fs, fn["length"])

    overall = Counter()
    for r in tables["transitions"].get(1, {}).values():
        overall.update(r)
    ctx = {"two_sided": two_sided_index(tables), "overall": dict(overall)}
    stats = Counter()

    base_tracks = []
    for m in range(cfg["num_tracks"]):
        rng = np.random.default_rng(np.random.SeedSequence(cfg["seed"], spawn_key=(ELABORATE_STREAM, m)))
        grid, frozen = initial_grid(cycle_steps, final)
        probs = division_probs(tables, cycle_steps, cfg, frozen)
        weights = np.asarray(probs[0], dtype=float)
        if weights.sum() == 0:
            raise ValueError("no division can be edited (p_start is 0 everywhere outside the final note)")
        weights /= weights.sum()
        targets = state_probs(tables, cycle_steps) if cfg["edit_rules"] == "input_states" else None
        cycles = [copy.deepcopy(grid)]
        for _ in range(cfg["num_cycles"] - 1):
            for _ in range(cfg["changes_per_cycle"]):
                if targets is None:
                    d = int(rng.choice(cycle_steps, p=weights))
                    apply_edit(grid, d, probs, frozen, tables, cfg, rng, stats, ctx)
                    continue
                # Draw until something actually changes, so each cycle differs from
                # the one before by changes_per_cycle real edits.
                for _ in range(10000):
                    d = int(rng.choice(cycle_steps, p=weights))
                    if apply_state_edit(grid, d, targets, frozen, tables, cfg, rng, stats, ctx):
                        break
                    stats["draws_without_change"] += 1
            cycles.append(copy.deepcopy(grid))
        base_tracks.append(cycles)

    rco = cfg["reverse_cycle_order"]
    orders = [("", False), ("C", True)] if rco == "both" else [("", bool(rco))]
    tracks = []
    for suffix, rev in orders:
        for m, cycles in enumerate(base_tracks):
            ordered = cycles[::-1] if rev else cycles
            out_index = len(tracks)
            notes, counts, busiest = [], [], []
            for n, g in enumerate(ordered):
                events = [e for e in el.grid_to_stepevents(g) if e.pitch is not None]
                vrng = lg.velocity_rng(cfg["seed"], out_index, n)
                for e in events:
                    v = lg.note_velocity(e.start_step, e.duration_steps, tables, cfg, vrng, stats)
                    notes.append((n * cycle_steps + e.start_step, e.duration_steps,
                                  lg.step_to_midi(e.pitch, tables), v))
                counts.append(len(events))
                if len(events) >= len(busiest):
                    busiest = [(e.start_step, e.duration_steps) for e in events]
            tracks.append({"name_suffix": f"{m + 1}{suffix}", "notes": notes,
                           "counts": counts, "busiest": busiest})
    return tracks, cycle_steps, stats


def check_grid(grid):
    """Invariants (for tests): no SUSTAIN at 0 and every SUSTAIN continues the pitch
    of the note before it."""
    assert grid.kind[0] != SUSTAIN
    for j in range(1, len(grid.kind)):
        if grid.kind[j] == SUSTAIN:
            assert grid.kind[j - 1] in (START, SUSTAIN) and grid.pitch[j] == grid.pitch[j - 1]
        if grid.kind[j] == REST:
            assert grid.pitch[j] is None
