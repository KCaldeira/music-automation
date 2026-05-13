"""Generate the 4 missing .xlsx table files for music-automation."""

import numpy as np
from openpyxl import Workbook
from pathlib import Path

OUTPUT_DIR = Path("data/input")

DIVISIONS_PER_BEAT = 4
BEATS_PER_BAR = 4
BARS_PER_CYCLE = 4
DIVISIONS_PER_BAR = DIVISIONS_PER_BEAT * BEATS_PER_BAR  # 16
DIVISIONS_PER_CYCLE = DIVISIONS_PER_BAR * BARS_PER_CYCLE  # 64
MAX_LENGTH = DIVISIONS_PER_BAR - 1  # 15


def write_1d(filename, header_a, header_b, data):
    wb = Workbook()
    ws = wb.active
    ws.append([header_a, header_b])
    for i, val in enumerate(data):
        ws.append([i, float(val)])
    wb.save(OUTPUT_DIR / filename)
    print(f"Created {filename}: {len(data)} rows")


def write_2d(filename, row_header, col_headers, data):
    wb = Workbook()
    ws = wb.active
    ws.append([row_header] + col_headers)
    for i, row in enumerate(data):
        ws.append([i] + [float(v) for v in row])
    wb.save(OUTPUT_DIR / filename)
    print(f"Created {filename}: {data.shape}")


def make_location_volume():
    """Louder on downbeats, moderate on other beats, softer on off-beats."""
    vol = np.zeros(DIVISIONS_PER_CYCLE)
    for loc in range(DIVISIONS_PER_CYCLE):
        pos_in_bar = loc % DIVISIONS_PER_BAR
        if pos_in_bar == 0:
            vol[loc] = 0.9  # downbeat
        elif pos_in_bar % DIVISIONS_PER_BEAT == 0:
            vol[loc] = 0.7  # beats 2, 3, 4
        elif pos_in_bar % (DIVISIONS_PER_BEAT // 2) == 0:
            vol[loc] = 0.5  # eighth note positions
        else:
            vol[loc] = 0.4  # sixteenth note positions
    write_1d("location_volume.xlsx", "location_in_cycle", "volume", vol)


def make_pitch_volume():
    """Neutral — all 1.0."""
    data = np.ones(12)
    write_1d("pitch_volume.xlsx", "pitch", "volume", data)


def make_note_length_table():
    """Bias toward 2-4 division notes on strong beats, 1-2 on weak beats."""
    table = np.zeros((DIVISIONS_PER_CYCLE, MAX_LENGTH))
    for loc in range(DIVISIONS_PER_CYCLE):
        pos_in_bar = loc % DIVISIONS_PER_BAR
        probs = np.zeros(MAX_LENGTH)
        if pos_in_bar == 0:
            # Downbeat: favor longer notes (2-8 divisions)
            probs[1] = 3.0  # len 2
            probs[2] = 4.0  # len 3
            probs[3] = 5.0  # len 4
            probs[4] = 3.0  # len 5
            probs[5] = 2.0  # len 6
            probs[6] = 1.5  # len 7
            probs[7] = 1.0  # len 8
        elif pos_in_bar % DIVISIONS_PER_BEAT == 0:
            # Other beats: favor 2-4 divisions
            probs[0] = 1.0  # len 1
            probs[1] = 4.0  # len 2
            probs[2] = 3.0  # len 3
            probs[3] = 4.0  # len 4
            probs[4] = 1.0  # len 5
        elif pos_in_bar % 2 == 0:
            # Eighth note positions: favor 1-2
            probs[0] = 3.0  # len 1
            probs[1] = 4.0  # len 2
            probs[2] = 1.0  # len 3
            probs[3] = 1.0  # len 4
        else:
            # Sixteenth note positions: favor 1
            probs[0] = 5.0  # len 1
            probs[1] = 2.0  # len 2
            probs[2] = 0.5  # len 3
        table[loc] = probs / probs.sum()
    write_2d(
        "note_length_table.xlsx",
        "location_in_cycle",
        [f"len_{i+1}" for i in range(MAX_LENGTH)],
        table,
    )


def make_rest_length_table():
    """Bias toward short rests (1-2 divisions)."""
    table = np.zeros((DIVISIONS_PER_CYCLE, MAX_LENGTH))
    for loc in range(DIVISIONS_PER_CYCLE):
        probs = np.zeros(MAX_LENGTH)
        probs[0] = 5.0  # len 1
        probs[1] = 3.0  # len 2
        probs[2] = 1.5  # len 3
        probs[3] = 1.0  # len 4
        probs[4] = 0.3  # len 5
        table[loc] = probs / probs.sum()
    write_2d(
        "rest_length_table.xlsx",
        "location_in_cycle",
        [f"len_{i+1}" for i in range(MAX_LENGTH)],
        table,
    )


if __name__ == "__main__":
    make_location_volume()
    make_pitch_volume()
    make_note_length_table()
    make_rest_length_table()
    print("Done.")
