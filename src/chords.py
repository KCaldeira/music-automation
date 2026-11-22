"""Chord parsing and chord-tone retargeting (learned workflow post-processing).

retarget_pitch() moves a note that was a tone of the source chord to the nearest
tone of the target chord. Notes that were not source-chord tones (passing tones)
are left alone. Ties (a target tone equally far above and below) are broken by
TIE_PREFERENCE: root over third, fifth over third, fifth over sixth, root over
seventh (Ken's rules); the order between pairs he did not rank (root/fifth,
fifth/seventh, sixth/root, third/seventh) follows the same list.
"""

from learner import PITCH_CLASSES

# Chord-tone roles, in tie-break order (earlier wins).
TIE_PREFERENCE = ("root", "fifth", "third", "sixth", "seventh")

# Suffix -> {role: semitones above the root}. Longest suffixes are tried first.
QUALITIES = {
    "":     {"root": 0, "third": 4, "fifth": 7},
    "m":    {"root": 0, "third": 3, "fifth": 7},
    "6":    {"root": 0, "third": 4, "fifth": 7, "sixth": 9},
    "m6":   {"root": 0, "third": 3, "fifth": 7, "sixth": 9},
    "7":    {"root": 0, "third": 4, "fifth": 7, "seventh": 10},
    "m7":   {"root": 0, "third": 3, "fifth": 7, "seventh": 10},
    "maj7": {"root": 0, "third": 4, "fifth": 7, "seventh": 11},
    "m7b5": {"root": 0, "third": 3, "fifth": 6, "seventh": 10},
    "dim":  {"root": 0, "third": 3, "fifth": 6},
    "dim7": {"root": 0, "third": 3, "fifth": 6, "seventh": 9},
}


def parse_chord(name):
    """'Em7' -> {pitch class: role}, e.g. {4: 'root', 7: 'third', 11: 'fifth', 2: 'seventh'}."""
    root = name[:2] if name[:2] in PITCH_CLASSES else name[:1]
    if root not in PITCH_CLASSES or name[len(root):] not in QUALITIES:
        raise ValueError(f"unknown chord {name!r}: expected a root (C, F#, Bb, ...) and one of "
                         f"{sorted(q or '(major)' for q in QUALITIES)}")
    r = PITCH_CLASSES[root]
    return {(r + iv) % 12: role for role, iv in QUALITIES[name[len(root):]].items()}


def retarget_pitch(pitch, source, target):
    """source, target: parsed chords. Returns the new MIDI pitch."""
    if pitch % 12 not in source:
        return pitch                      # passing tone: unchanged
    for distance in range(12):
        candidates = [p for p in {pitch - distance, pitch + distance} if p % 12 in target]
        if candidates:
            return min(candidates, key=lambda p: TIE_PREFERENCE.index(target[p % 12]))
    raise AssertionError("a chord has at least one tone within an octave")
