"""List runs from a JSONL file to help pick the clearest one for each scene.

    python pick_runs.py runs/qwen38_27b_E1_none.jsonl
    python pick_runs.py runs/qwen38_27b_E1_none.jsonl --answer canary --told-user

Shows, per run: outcome, answer, door tried/reached, told-user, tripwire, and
the first line of the final answer. Tripwire runs are flagged loudly: they are
bugs and must never be shown (plan section 9).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path):
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


def first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0][:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--answer", help="only runs whose answer is this (canary, fact, guess, none, ...)")
    ap.add_argument("--door-reached", action="store_true", help="only runs that reached a door")
    ap.add_argument("--told-user", action="store_true", help="only door runs that told the user")
    args = ap.parse_args()

    rows = list(load(args.path))
    shown = 0
    tripwires = []
    for r in rows:
        s = r["score"]
        if s.get("tripwire"):
            tripwires.append(r["run_id"])
        if args.answer and s["answer"] != args.answer:
            continue
        if args.door_reached and not s["door_reached"]:
            continue
        if args.told_user and not s.get("told_user"):
            continue
        shown += 1
        flag = "  !!! TRIPWIRE" if s.get("tripwire") else ""
        print(f"{r['run_id']}")
        print(f"    outcome={r['outcome']} answer={s['answer']} "
              f"door_tried={s['door_tried']} door_reached={s['door_reached']} "
              f"told_user={s.get('told_user')}{flag}")
        print(f"    final: {first_line(r.get('final'))}")

    print(f"\n{shown}/{len(rows)} runs shown.")
    if tripwires:
        print(f"WARNING: {len(tripwires)} tripwire run(s) in this file - investigate, never show:")
        for rid in tripwires:
            print("  -", rid)


if __name__ == "__main__":
    main()
