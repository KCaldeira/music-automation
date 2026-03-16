# music-automation

This project produces MIDI tracks that serve as the foundation of a computer-generated part of a song, intended for later editing in Cubase.

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

1. **`note_probability_table`** — Vector of length `divisions_per_cycle`. Probability of a note starting on a particular beat location within the cycle (e.g., the probability of a note starting on the 16th note before the 3rd beat of the measure).

2. **`rest_length_table`** — Array of dimensions `divisions_per_cycle` by (`divisions_per_bar` - 1). The probabilities of rest length as a function of beat location within the cycle.

3. **`note_length_table`** — Array of dimensions `divisions_per_cycle` by (`divisions_per_bar` - 1). The probabilities of note length as a function of beat location within the cycle.

4. **`pitch_probability_table`** — Vector of length 12. Relative frequency of notes within the key. This is cyclic and applies equally to different octaves; `pitch_probability_table[0]` is the probability of being `base_pitch` or octaves above/below `base_pitch`.

5. **`interval_probability_table`** — Vector of length 25. Relative frequency of different note intervals:
   - `interval_probability_table[0]` — probability of going down an octave
   - `interval_probability_table[12]` — probability of no change
   - `interval_probability_table[24]` — probability of going up an octave

6. **`location_volume`** — Vector of length `divisions_per_cycle` giving the mean volume on a 0-to-1 scale.

7. **`volume_variability`** — A real number indicating the standard deviation of the volume around the mean.

8. **`pitch_volume`** — Vector of length 12 that multiplies `location_volume` depending on note pitch.

9. **`pitch_gravity`** — Value indicating how likely the pitch transition table brings notes back to `base_pitch`. The maximum distance a note can get from `base_pitch` is 2 * `pitch_gravity`.

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

### Determining Note/Rest Start and Length

Assume the current note location is `current_location`, where the start of the first beat has value 0 and this increases by 1 for each beat division. The location of the start of the next bar is `divisions_per_bar`.

```
current_cycle_location = current_location mod divisions_per_cycle
```

1. Determine whether this will be a **note** or **rest** using `note_probability_table[current_cycle_location]`.
2. If it is a **rest**, the rest length is sampled from `rest_length_table[current_cycle_location]`, where the first element is the probability of a length of one division and the last element is the probability of a length of `divisions_per_bar - 1`.
3. If it is a **note**, the note length is sampled from `note_length_table[current_cycle_location]`, where the first element is the probability of a length of one division and the last element is the probability of a length of `divisions_per_bar - 1`.
4. Increment `current_location` by the note or rest duration, then repeat.

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
