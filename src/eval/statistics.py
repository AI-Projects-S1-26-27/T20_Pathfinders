"""Deterministic summaries, bootstrap confidence intervals and paired tests."""
from __future__ import annotations
from collections import defaultdict
import math
import random
import statistics


def mean_ci(values, confidence=0.95, n_boot=5000, seed=20261118):
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    if not vals:
        return {"n": 0, "mean": None, "ci_low": None, "ci_high": None}
    mean = statistics.mean(vals)
    if len(vals) == 1:
        return {"n": 1, "mean": mean, "ci_low": None, "ci_high": None}
    rnd = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [vals[rnd.randrange(len(vals))] for _ in vals]
        boots.append(statistics.mean(sample))
    boots.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = boots[int(alpha * (n_boot - 1))]
    hi = boots[int((1.0 - alpha) * (n_boot - 1))]
    return {"n": len(vals), "mean": mean, "ci_low": lo, "ci_high": hi}


def paired_bootstrap(a_by_seed, b_by_seed, confidence=0.95, n_boot=5000, seed=20261118):
    """CI for A-B using only seeds present in both conditions."""
    common = sorted(set(a_by_seed) & set(b_by_seed))
    diffs = [float(a_by_seed[s]) - float(b_by_seed[s]) for s in common
             if a_by_seed[s] is not None and b_by_seed[s] is not None]
    if not diffs:
        return {"n_pairs": 0, "mean_difference": None, "ci_low": None, "ci_high": None}
    mean = statistics.mean(diffs)
    if len(diffs) == 1:
        return {"n_pairs": 1, "mean_difference": mean, "ci_low": None, "ci_high": None}
    rnd = random.Random(seed)
    boots = [statistics.mean(diffs[rnd.randrange(len(diffs))] for _ in diffs)
             for _ in range(n_boot)]
    boots.sort()
    alpha = (1-confidence)/2
    return {"n_pairs": len(diffs), "mean_difference": mean,
            "ci_low": boots[int(alpha*(n_boot-1))],
            "ci_high": boots[int((1-alpha)*(n_boot-1))]}


def summarize(rows, metrics):
    grouped = defaultdict(list)
    for row in rows:
        key = (row.get("scenario"), row.get("rate"), row.get("mode"))
        grouped[key].append(row)
    output = []
    for (scenario, rate, mode), group in sorted(grouped.items(), key=lambda x: str(x[0])):
        for metric in metrics:
            ci = mean_ci([r.get(metric) for r in group])
            output.append({"scenario": scenario, "rate": rate, "mode": mode,
                           "metric": metric, **ci})
    return output


def paired_comparisons(rows, metrics, baseline="centralized"):
    by_cell = defaultdict(dict)
    for row in rows:
        by_cell[(row.get("scenario"), row.get("rate"))][row.get("mode"), row.get("seed")] = row
    out = []
    for (scenario, rate), cell in sorted(by_cell.items(), key=lambda x: str(x[0])):
        modes = sorted({m for m, _ in cell if m != baseline})
        seeds = {s for m, s in cell if m == baseline}
        for mode in modes:
            paired_seeds = seeds & {s for m, s in cell if m == mode}
            for metric in metrics:
                a = {s: cell[(mode, s)].get(metric) for s in paired_seeds}
                b = {s: cell[(baseline, s)].get(metric) for s in paired_seeds}
                ci = paired_bootstrap(a, b)
                out.append({"scenario": scenario, "rate": rate,
                            "comparison": f"{mode} - {baseline}", "metric": metric, **ci})
    return out
