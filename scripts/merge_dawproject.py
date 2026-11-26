"""Merge several note-track .dawproject files (as written by src/dawproject_writer.py)
into one, so they load in a single import.

    python scripts/merge_dawproject.py OUT.dawproject IN1.dawproject IN2.dawproject ...
        [--label-pattern REGEX]

Every note track of every input is copied (notes, lengths, velocities) in input
order. Track names get a prefix taken from each input's file name: the first match
of --label-pattern (default: the cycle length, e.g. "2bar"), else the input's
position ("f1", "f2", ...). Tempo and time signature come from the first input;
inputs with a different tempo or signature are refused.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import dawproject_writer


def read_dawproject(path):
    """(tempo, (numerator, denominator), [(name, length_beats, notes)]), notes as
    (time_beats, duration_beats, key, velocity 0-127)."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("project.xml"))
    tempo = float(root.find("Transport/Tempo").get("value"))
    sig = root.find("Transport/TimeSignature")
    signature = (int(sig.get("numerator")), int(sig.get("denominator")))
    names = {t.get("id"): t.get("name") for t in root.iter("Track") if t.get("contentType") == "notes"}
    tracks = []
    for lanes in root.find("Arrangement/Lanes"):
        tid = lanes.get("track")
        if tid not in names:
            continue
        clips = lanes.findall("Clips/Clip")
        if len(clips) != 1 or clips[0].get("time") not in ("0.0", "0"):
            raise ValueError(f"{path}: track {names[tid]!r} must have one clip starting at 0")
        clip = clips[0]
        notes = [(float(n.get("time")), float(n.get("duration")), int(n.get("key")),
                  round(float(n.get("vel")) * 127)) for n in clip.iter("Note")]
        tracks.append((names[tid], float(clip.get("duration")), notes))
    return tempo, signature, tracks


def main():
    parser = argparse.ArgumentParser(description="Merge .dawproject files into one.")
    parser.add_argument("output")
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--label-pattern", default=r"\d+bar")
    args = parser.parse_args()

    merged, tempo, signature = [], None, None
    for i, path in enumerate(args.inputs, 1):
        t, sig, tracks = read_dawproject(path)
        if tempo is None:
            tempo, signature = t, sig
        elif abs(t - tempo) > 1e-6 or sig != signature:
            raise ValueError(f"{path}: tempo {t} {sig} differs from the first input's {tempo} {signature}")
        m = re.search(args.label_pattern, Path(path).stem)
        label = m.group(0) if m else f"f{i}"
        for name, length, notes in tracks:
            short = name.split()[-1]                 # e.g. "t5" from "N58 pno t5"
            merged.append((f"{label} {short}", length, notes))
        print(f"  {label:6s} {len(tracks)} tracks, {sum(len(n) for _, _, n in tracks)} notes  <- {Path(path).name}")

    names = [n for n, _, _ in merged]
    if len(set(names)) != len(names):
        raise ValueError("track names would repeat; use a --label-pattern that tells the inputs apart")
    out = Path(args.output)
    dawproject_writer.write_dawproject(
        out, merged, tempo, signature[0], signature[1], title=out.stem,
        comment="Merged from: " + ", ".join(Path(p).name for p in args.inputs))
    print(f"Wrote   {out}  ({len(merged)} tracks, {tempo} bpm, {signature[0]}/{signature[1]})")


if __name__ == "__main__":
    main()
