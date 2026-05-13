"""Load and validate a generator config JSON file."""

import json
from pathlib import Path


REQUIRED_KEYS = (
    "tempo",
    "beats_per_bar",
    "divisions_per_beat",
    "bars_per_cycle",
    "base_pitch",
    "note_probability",
    "beat_start_probability",
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

    cfg.setdefault("include_reversed_tracks", False)

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
    if len(cfg["beat_start_probability"]) != expected_beat_len:
        fail(
            f"beat_start_probability must have length "
            f"divisions_per_beat * beats_per_bar = {expected_beat_len} "
            f"(got {len(cfg['beat_start_probability'])})"
        )

    if any(w < 0 for w in cfg["note_probability"]):
        fail("note_probability entries must be non-negative")
    if any(w < 0 for w in cfg["beat_start_probability"]):
        fail("beat_start_probability entries must be non-negative")
    if sum(cfg["note_probability"]) <= 0:
        fail("note_probability must have at least one positive entry")
    if sum(cfg["beat_start_probability"]) <= 0:
        fail("beat_start_probability must have at least one positive entry")

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

    if not isinstance(cfg["output_dir"], str) or not cfg["output_dir"]:
        fail("output_dir must be a non-empty string")
