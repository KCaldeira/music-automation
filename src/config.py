"""Load and validate a generator config JSON file.

Two pathways share this module: load_config() for the stochastic generator and
load_elaboration_config() for the elaboration generator.
"""

import json
from pathlib import Path


REQUIRED_KEYS = (
    "tempo",
    "beats_per_bar",
    "divisions_per_beat",
    "bars_per_cycle",
    "base_pitch",
    "note_probability",
    "division_start_probability",
    "step_length_scale",
    "interval_gravity",
    "pitch_gravity",
    "ending_gravity",
    "max_pitch_range",
    "rest_probability",
    "num_tracks",
    "total_cycles",
    "output_dir",
)


def load_config(path: str) -> dict:
    cfg = json.loads(Path(path).read_text())

    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"Config {path} is missing required keys: {missing}")

    cfg.setdefault("random_number_change_probability", 1.0)
    cfg.setdefault("start_cycle_on_base_pitch", False)
    cfg.setdefault("seed", None)
    cfg.setdefault("track_direction", "both")
    cfg.setdefault("reversal_last_note_start_step", 0)

    _validate(cfg, path)
    return cfg


def _validate(cfg: dict, path: str) -> None:
    def fail(msg: str) -> None:
        raise ValueError(f"Invalid config {path}: {msg}")

    for k in ("beats_per_bar", "divisions_per_beat", "bars_per_cycle",
              "num_tracks", "total_cycles"):
        if not isinstance(cfg[k], int) or cfg[k] < 1:
            fail(f"{k} must be a positive int (got {cfg[k]!r})")

    if not isinstance(cfg["tempo"], (int, float)) or cfg["tempo"] <= 0:
        fail(f"tempo must be > 0 (got {cfg['tempo']!r})")

    if len(cfg["note_probability"]) != 12:
        fail(f"note_probability must have length 12 (got {len(cfg['note_probability'])})")

    expected_beat_len = cfg["divisions_per_beat"] * cfg["beats_per_bar"]
    if len(cfg["division_start_probability"]) != expected_beat_len:
        fail(
            f"division_start_probability must have length "
            f"divisions_per_beat * beats_per_bar = {expected_beat_len} "
            f"(got {len(cfg['division_start_probability'])})"
        )

    if any(w < 0 for w in cfg["note_probability"]):
        fail("note_probability entries must be non-negative")
    if any(w < 0 for w in cfg["division_start_probability"]):
        fail("division_start_probability entries must be non-negative")
    if sum(cfg["note_probability"]) <= 0:
        fail("note_probability must have at least one positive entry")
    if sum(cfg["division_start_probability"]) <= 0:
        fail("division_start_probability must have at least one positive entry")

    for k in ("interval_gravity", "pitch_gravity",
              "step_length_scale", "ending_gravity"):
        if not isinstance(cfg[k], (int, float)) or cfg[k] <= 0:
            fail(f"{k} must be > 0 (got {cfg[k]!r})")

    if not isinstance(cfg["max_pitch_range"], int) or cfg["max_pitch_range"] < 0:
        fail(f"max_pitch_range must be a non-negative int (got {cfg['max_pitch_range']!r})")

    base = cfg["base_pitch"]
    if not isinstance(base, int) or not (0 <= base <= 127):
        fail(f"base_pitch must be an int in [0, 127] (got {base!r})")

    low, high = base - cfg["max_pitch_range"], base + cfg["max_pitch_range"]
    if low < 0 or high > 127:
        fail(
            f"base_pitch ± max_pitch_range must stay within [0, 127] "
            f"(got [{low}, {high}])"
        )

    if not 0 <= cfg["rest_probability"] <= 1:
        fail(f"rest_probability must be in [0, 1] (got {cfg['rest_probability']!r})")

    p = cfg["random_number_change_probability"]
    if not isinstance(p, (int, float)) or not 0 <= p <= 1:
        fail(f"random_number_change_probability must be in [0, 1] (got {p!r})")

    if not isinstance(cfg["start_cycle_on_base_pitch"], bool):
        fail(f"start_cycle_on_base_pitch must be a bool (got {cfg['start_cycle_on_base_pitch']!r})")

    if not isinstance(cfg["output_dir"], str) or not cfg["output_dir"]:
        fail("output_dir must be a non-empty string")

    if "description" in cfg and not isinstance(cfg["description"], str):
        fail(f"description must be a string (got {cfg['description']!r})")

    if cfg["seed"] is not None and (not isinstance(cfg["seed"], int)
                                    or isinstance(cfg["seed"], bool) or cfg["seed"] < 0):
        fail(f"seed must be null or a non-negative int (got {cfg['seed']!r})")

    if cfg["track_direction"] not in ("forward", "reversed", "both"):
        fail(f"track_direction must be 'forward', 'reversed', or 'both' "
             f"(got {cfg['track_direction']!r})")

    n = cfg["reversal_last_note_start_step"]
    if isinstance(n, bool) or not isinstance(n, int) or n > 0:
        fail(f"reversal_last_note_start_step must be an int <= 0 (got {n!r})")


# --------------------------------------------------------------------------- #
# Elaboration pathway
# --------------------------------------------------------------------------- #

ELAB_REQUIRED_KEYS = (
    "tempo",
    "beats_per_bar",
    "divisions_per_beat",
    "bars_per_cycle",
    "base_pitch",
    "note_probability",
    "max_pitch_range",
    "interval_gravity",
    "pitch_gravity",
    "division_change_probability",
    "division_rest_probability",
    "division_start_probability",
    "num_tracks",
    "total_cycles",
    "output_dir",
)


def normalize_division_vector(value, divisions_per_bar, divisions_per_cycle, name):
    """Expand a per-division config value to a full divisions_per_cycle list.

    Accepts a scalar (broadcast to every division), a divisions_per_bar-length
    list (tiled bars_per_cycle times), or a divisions_per_cycle-length list
    (used as-is). Raises ValueError on any other shape.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number or list, not a bool")
    if isinstance(value, (int, float)):
        return [float(value)] * divisions_per_cycle
    if isinstance(value, list):
        if len(value) == divisions_per_cycle:
            return [float(v) for v in value]
        if len(value) == divisions_per_bar:
            bars = divisions_per_cycle // divisions_per_bar
            return [float(v) for v in value] * bars
    raise ValueError(
        f"{name} must be a scalar, a list of length divisions_per_bar "
        f"({divisions_per_bar}), or divisions_per_cycle ({divisions_per_cycle}); "
        f"got {value!r}"
    )


def load_elaboration_config(path: str) -> dict:
    cfg = json.loads(Path(path).read_text())

    missing = [k for k in ELAB_REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"Config {path} is missing required keys: {missing}")

    cfg.setdefault("changes_per_cycle", 1)
    cfg.setdefault("reverse_cycle_order", True)
    cfg.setdefault("seed", None)

    _validate_elaboration(cfg, path)
    return cfg


def _validate_elaboration(cfg: dict, path: str) -> None:
    def fail(msg: str) -> None:
        raise ValueError(f"Invalid config {path}: {msg}")

    for k in ("beats_per_bar", "divisions_per_beat", "bars_per_cycle",
              "num_tracks", "total_cycles", "changes_per_cycle"):
        if not isinstance(cfg[k], int) or isinstance(cfg[k], bool) or cfg[k] < 1:
            fail(f"{k} must be a positive int (got {cfg[k]!r})")

    if not isinstance(cfg["tempo"], (int, float)) or cfg["tempo"] <= 0:
        fail(f"tempo must be > 0 (got {cfg['tempo']!r})")

    if len(cfg["note_probability"]) != 12:
        fail(f"note_probability must have length 12 (got {len(cfg['note_probability'])})")
    if any(w < 0 for w in cfg["note_probability"]):
        fail("note_probability entries must be non-negative")
    if sum(cfg["note_probability"]) <= 0:
        fail("note_probability must have at least one positive entry")

    for k in ("interval_gravity", "pitch_gravity"):
        if not isinstance(cfg[k], (int, float)) or isinstance(cfg[k], bool) or cfg[k] <= 0:
            fail(f"{k} must be > 0 (got {cfg[k]!r})")

    if (not isinstance(cfg["max_pitch_range"], int) or isinstance(cfg["max_pitch_range"], bool)
            or cfg["max_pitch_range"] < 0):
        fail(f"max_pitch_range must be a non-negative int (got {cfg['max_pitch_range']!r})")

    base = cfg["base_pitch"]
    if not isinstance(base, int) or isinstance(base, bool) or not (0 <= base <= 127):
        fail(f"base_pitch must be an int in [0, 127] (got {base!r})")
    low, high = base - cfg["max_pitch_range"], base + cfg["max_pitch_range"]
    if low < 0 or high > 127:
        fail(f"base_pitch ± max_pitch_range must stay within [0, 127] (got [{low}, {high}])")

    if not isinstance(cfg["reverse_cycle_order"], bool):
        fail(f"reverse_cycle_order must be a bool (got {cfg['reverse_cycle_order']!r})")

    if not isinstance(cfg["output_dir"], str) or not cfg["output_dir"]:
        fail("output_dir must be a non-empty string")

    if "description" in cfg and not isinstance(cfg["description"], str):
        fail(f"description must be a string (got {cfg['description']!r})")

    if cfg["seed"] is not None and (not isinstance(cfg["seed"], int)
                                    or isinstance(cfg["seed"], bool) or cfg["seed"] < 0):
        fail(f"seed must be null or a non-negative int (got {cfg['seed']!r})")

    # Normalize per-division vectors to full divisions_per_cycle length in place.
    dpb = cfg["divisions_per_beat"] * cfg["beats_per_bar"]
    dpc = dpb * cfg["bars_per_cycle"]
    for k in ("division_change_probability", "division_rest_probability",
              "division_start_probability"):
        try:
            cfg[k] = normalize_division_vector(cfg[k], dpb, dpc, k)
        except ValueError as e:
            fail(str(e))

    # division_change_probability is a WEIGHT vector: non-negative, positive sum.
    if any(w < 0 for w in cfg["division_change_probability"]):
        fail("division_change_probability entries must be non-negative")
    if sum(cfg["division_change_probability"]) <= 0:
        fail("division_change_probability must have at least one positive entry")

    # rest / start are DIRECT probabilities: each entry in [0, 1].
    for k in ("division_rest_probability", "division_start_probability"):
        if any(not (0.0 <= v <= 1.0) for v in cfg[k]):
            fail(f"{k} entries must each be in [0, 1]")
