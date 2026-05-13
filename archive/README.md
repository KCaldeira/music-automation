# music-automation

This project produces MIDI tracks that serve as the foundation of a computer-generated part of a song, intended for later editing in Cubase.

## Usage

Run the generator with a JSON configuration file:

```bash
python main.py --config data/music02/music02.json
```

Optional arguments:
- `--seed <int>` — Random seed for reproducibility

Output files are written to the directory specified by `table_dir` in the config.

## JSON Configuration File Format

The configuration file is a JSON object with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `tempo` | int | Tempo in BPM |
| `beats_per_bar` | int | Number of beats per bar |
| `divisions_per_beat` | int | Number of time divisions per beat (e.g., 4 means 16th notes if the beat is a quarter note) |
| `bars_per_cycle` | int | Number of bars in a probability/transition table cycle |
| `base_pitch` | int | Base MIDI pitch for the generated tones (e.g., 60 = middle C) |
| `volume_variability` | float | Standard deviation of volume around the mean (0-1 scale) |
| `pitch_gravity` | int | How strongly pitches are pulled toward `base_pitch`; max distance from base is 2 × this value |
| `num_tracks` | int | Number of MIDI tracks to generate |
| `total_cycles` | int | Total number of cycles to generate |
| `table_dir` | string | Path to directory containing the `.xlsx` probability tables |

### Example Configuration

```json
{
  "tempo": 84,
  "beats_per_bar": 4,
  "divisions_per_beat": 4,
  "bars_per_cycle": 4,
  "base_pitch": 60,
  "volume_variability": 0.05,
  "pitch_gravity": 24,
  "num_tracks": 12,
  "total_cycles": 200,
  "table_dir": "data/music02"
}
```

### Required Table Files

The `table_dir` directory must contain the following `.xlsx` files:

- `note_probability_table.xlsx`
- `rest_probability_table.xlsx`
- `note_length_table.xlsx`
- `rest_length_table.xlsx`
- `pitch_probability_table.xlsx`
- `interval_probability_table.xlsx`
- `location_volume.xlsx`
- `pitch_volume.xlsx`

## User Parameters

| Parameter | Description |
|-----------|-------------|
| `tempo` | Tempo in BPM |
| `beats_per_bar` | Number of beats per bar |
| `divisions_per_beat` | Number of time divisions per beat (e.g., 4 means 16th notes if the beat is a quarter note) |
| `bars_per_cycle` | Number of bars in a probability/transition table cycle |
| `base_pitch` | Base pitch for the generated tones |

### Derived Values

- `divisions_per_bar` = `divisions_per_beat` * `beats_per_bar`
- `divisions_per_cycle` = `divisions_per_beat` * `beats_per_bar` * `bars_per_cycle`

## Probability and Transition Tables

1. **`note_probability_table`** — Vector of length `divisions_per_cycle`. Conditional probability P(note | previous=note): probability the current event is a note given the previous event was a note.

2. **`rest_probability_table`** — Vector of length `divisions_per_cycle`. Conditional probability P(rest | previous=rest): probability the current event is a rest given the previous event was a rest.

3. **`rest_length_table`** — Array of dimensions `divisions_per_cycle` by (`divisions_per_bar`). The probabilities of rest length as a function of beat location within the cycle.

4. **`note_length_table`** — Array of dimensions `divisions_per_cycle` by (`divisions_per_bar`). The probabilities of note length as a function of beat location within the cycle.

5. **`pitch_probability_table`** — Vector of length 12. Relative frequency of notes within the key. This is cyclic and applies equally to different octaves; `pitch_probability_table[0]` is the probability of being `base_pitch` or octaves above/below `base_pitch`.

6. **`interval_probability_table`** — Vector of length 25. Relative frequency of different note intervals:
   - `interval_probability_table[0]` — probability of going down an octave
   - `interval_probability_table[12]` — probability of no change
   - `interval_probability_table[24]` — probability of going up an octave

7. **`location_volume`** — Vector of length `divisions_per_cycle` giving the mean volume on a 0-to-1 scale.

8. **`volume_variability`** — A real number indicating the standard deviation of the volume around the mean.

9. **`pitch_volume`** — Vector of length 12 that multiplies `location_volume` depending on note pitch.

10. **`pitch_gravity`** — Value indicating how likely the pitch transition table brings notes back to `base_pitch`. The maximum distance a note can get from `base_pitch` is 2 * `pitch_gravity`.

## Algorithms

### Determining the Next Pitch

Given the current pitch `pitch_current`, construct an `augmented_interval_probability_table`:

```
augmented_interval_probability_table[i] =
    interval_probability_table[i] * pitch_probability_table[(i - 12 + pitch_current - base_pitch) mod 12]
```

Apply a transformation to keep the notes close to the base pitch:

```
augmented_interval_probability_table[i] =
    max(0, augmented_interval_probability_table[i] * (1 - (i - 12 + pitch_current - base_pitch) / pitch_gravity))
```

Normalize:

```
augmented_interval_probability_table =
    augmented_interval_probability_table / sum(augmented_interval_probability_table)
```

The resulting table gives the probability of the next interval, where the first and last values are the probabilities of going down or up an octave, and the 13th value (index 12) is the probability of no change.

### Determining Note/Rest Start and Length (Markov Model)

The system uses a first-order Markov chain. State variable `previous_was_note` starts as `False` (silence before track).

Assume the current note location is `current_location`, where the start of the first beat has value 0 and this increases by 1 for each beat division. The location of the start of the next bar is `divisions_per_bar`.

```
current_cycle_location = current_location mod divisions_per_cycle
```

1. **Determine note or rest using Markov transition:**
   - If `previous_was_note == True`: P(note) = `note_probability_table[current_cycle_location]`
   - If `previous_was_note == False`: P(note) = 1 - `rest_probability_table[current_cycle_location]`

2. Sample from Bernoulli distribution with computed probability.

3. If it is a **rest**, the rest length is sampled from `rest_length_table[current_cycle_location]`, where the first element is the probability of a length of one division and the last element is the probability of a length of `divisions_per_bar`.

4. If it is a **note**, the note length is sampled from `note_length_table[current_cycle_location]`, where the first element is the probability of a length of one division and the last element is the probability of a length of `divisions_per_bar`.

5. Update `previous_was_note = is_note`.

6. Increment `current_location` by the note or rest duration, then repeat.

### Note Volume

```
volume = min(1, max(0,
    location_volume[current_cycle_location]
    * pitch_volume[pitch_current mod 12]
    * (1 + volume_variability * normal(0, 1))
))
```

## Output Format

All generated tracks are output as individual tracks in a single MIDI file.

### Cycle Sorting by Mean Pitch

After generating all tracks, the system computes a duration-weighted mean pitch for each (track, cycle) pair:

```
mean_pitch = sum(pitch_i * duration_i) / sum(duration_i)
```

For each cycle index, the track segments are sorted by mean pitch (descending) and redistributed:
- Track 0 receives the segment with the highest mean pitch
- Last track receives the segment with the lowest mean pitch

This maintains phrase continuity within each cycle while creating pitch continuity across tracks.

**Output files:**
- `output_YYYYMMDD_HHMMSS_original.mid` - Unsorted tracks
- `output_YYYYMMDD_HHMMSS_sorted.mid` - Tracks sorted by mean pitch per cycle

Both files are placed in the same directory as the input tables (`table_dir`).
