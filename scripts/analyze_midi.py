"""Learned workflow, stage 1: analyse a MIDI melody into a hand-editable tables file.

    python scripts/analyze_midi.py config/learned/<name>.json

Reads the MIDI named in the config, cleans it, builds the tables described in
docs/learned_workflow.md (sections 2-3), writes them to the config's
output_path and prints a short report. Generation reads only that file, so it
can be edited by hand before generating.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import learner

REPORT_THRESHOLD = 3  # for the coverage summary only; generation has its own thresholds


def print_report(doc, report):
    c = doc["cleaning"]
    src = report["source"]
    print(f"Input   {doc['source_midi']}  ({src['tempo_bpm']} bpm, {src['time_signature']}, "
          f"{report['bars']} bars)")
    if src["unmatched_note_offs"] or src["notes_never_ended"]:
        print(f"        warning: {src['unmatched_note_offs']} unmatched note-offs, "
              f"{src['notes_never_ended']} notes never ended (ignored)")
    print(f"Clean   {c['raw_notes']} notes -> {c['notes_after_cleaning']}: "
          f"{c['moved_by_quantizing']} moved by quantizing, "
          f"{c['dropped_zero_length']} zero-length dropped, "
          f"{c['dropped_same_start']} same-start dropped, "
          f"{c['cut_at_next_start']} cut at next start, "
          f"{c['snapped_to_key']} snapped to key")
    print(f"Key     {doc['root']} {doc['mode'] or doc['scale_semitones']}, chord {doc['chord']}, "
          f"base_pitch {doc['base_pitch']}; MIDI {report['midi_range'][0]}-{report['midi_range'][1]}, "
          f"steps {report['step_range'][0]}..{report['step_range'][1]}")
    print("Degrees " + "  ".join(f"{k}:{v}" for k, v in report["degree_class_totals"].items()))
    print("p_start " + " ".join(f"{p:.2f}" for p in doc["onsets"]["p_start"]))
    print("starts  " + " ".join(str(n) for n in doc["onsets"]["n_start"]))
    thin = [s for s, cnt in doc["position_pitch"].items() if sum(cnt.values()) < REPORT_THRESHOLD]
    print(f"Positions with < {REPORT_THRESHOLD} notes (pitch table would be ignored): {thin or 'none'}")
    for k, table in sorted(doc["transitions"].items()):
        totals = [sum(c.values()) for c in table.values()]
        ok = [t for t in totals if t >= REPORT_THRESHOLD]
        share = sum(ok) / sum(totals) if totals else 0
        print(f"k={k}     {len(totals)} contexts, {len(ok)} with >= {REPORT_THRESHOLD} "
              f"(covering {share:.0%} of note instances)")


def main():
    parser = argparse.ArgumentParser(description="Analyse a MIDI melody into probability tables.")
    parser.add_argument("config_path", help="analysis config, e.g. config/learned/N59_analyze_N56_Ephrygian.json")
    args = parser.parse_args()

    cfg = learner.load_analysis_config(args.config_path)
    doc, report = learner.analyze(cfg)

    out = Path(cfg["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(learner.format_tables(doc))

    print_report(doc, report)
    print(f"Wrote   {out}")


if __name__ == "__main__":
    main()
