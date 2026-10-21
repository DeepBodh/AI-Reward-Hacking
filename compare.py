"""Optional section 16 helper: count side-door and made-up rates for two run
sets (e.g. censored vs uncensored) and report each with a 95% confidence range,
plus the range of the difference. If the difference's range includes zero, there
is no clear difference. See plan section 16 on Confluence.

    python compare.py runs/censored_E1_none.jsonl runs/uncensored_E1_none.jsonl

This is the one place the demo shows a number. Everything else is staged scenes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from statsmodels.stats.proportion import confint_proportions_2indep, proportion_confint


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def counts(rows: list[dict]) -> dict:
    n = len(rows)
    door = sum(1 for r in rows if r["score"]["door_reached"])
    fact = sum(1 for r in rows if r["score"]["answer"] == "fact")
    return {"n": n, "door_reached": door, "fact": fact}


def rate_ci(hits: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    lo, hi = proportion_confint(hits, n, alpha=0.05, method="wilson")
    return hits / n, lo, hi


def diff_ci(h1: int, n1: int, h2: int, n2: int) -> tuple[float, float, float]:
    lo, hi = confint_proportions_2indep(h1, n1, h2, n2, method="newcomb")
    return h1 / n1 - h2 / n2, lo, hi


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def report(label_a: str, a: dict, label_b: str, b: dict, metric: str) -> None:
    ra, la, ha = rate_ci(a[metric], a["n"])
    rb, lb, hb = rate_ci(b[metric], b["n"])
    print(f"  {label_a}: {a[metric]}/{a['n']} = {pct(ra)} (95% CI {pct(la)}-{pct(ha)})")
    print(f"  {label_b}: {b[metric]}/{b['n']} = {pct(rb)} (95% CI {pct(lb)}-{pct(hb)})")
    if a["n"] and b["n"]:
        d, dlo, dhi = diff_ci(a[metric], a["n"], b[metric], b["n"])
        verdict = "no clear difference (range includes 0)" if dlo <= 0 <= dhi else "a real difference"
        print(f"  difference: {pct(d)} (95% CI {pct(dlo)} to {pct(dhi)}) -> {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file_a", type=Path)
    ap.add_argument("file_b", type=Path)
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    args = ap.parse_args()
    a, b = counts(load(args.file_a)), counts(load(args.file_b))
    print("side-door rate:")
    report(args.label_a, a, args.label_b, b, "door_reached")
    print("made-up (fact) rate:")
    report(args.label_a, a, args.label_b, b, "fact")
    print("\nEvery number carries its run count, model version, date and setup.")


if __name__ == "__main__":
    main()
