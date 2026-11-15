"""
R3 · Fill docs/data_profile.md from the real profile (the "___" blanks and the Results table).

    python scripts/fill_data_profile.py

Reads data/profile/summary.json and data/profile/packages.csv, written by
    python -m src.data.profile_amazon        (see docs/data_profile.md, steps 1-3)
and rewrites docs/data_profile.md in place. Run it again after a new profile; it finds the lines by
their labels, not by line number, so it also works on an already filled doc.

Options: --profile-dir data/profile --doc docs/data_profile.md
"""

from __future__ import annotations

import argparse
import sys
import json
import re
from pathlib import Path

ROWS = [  # (label in the doc, key in summary.json, scale, decimals)
    ("Stops per route", "stops_per_route", 1, 0),
    ("Packages per stop", "packages_per_stop", 1, 1),
    ("Package volume (cm³)", "package_volume_cm3", 1, 0),
    ("Time-window width (h)", "tw_width_h", 1, 2),
    ("Service time per stop (s)", "service_time_per_stop_s(sum)", 1, 0),
    ("Capacity utilization", "capacity_utilization", 100, 1),  # shown in %
]
QUANTILES = ["0.05", "0.25", "0.5", "0.75", "0.95", "mean"]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # "python scripts/..." finds src/


def fmt(x, decimals, scale=1):
    if x is None:
        return "n/a"
    v = float(x) * scale
    return f"{v:,.{decimals}f}" + ("%" if scale == 100 else "")


def service_time_unit(packages_csv: Path) -> str:
    """Per package or per stop? If every package at a stop carries the same value, it is one
    stop-level value repeated on each package; otherwise it is per package."""
    import pandas as pd

    from src.data.orders_from_amazon import service_is_per_stop

    P = pd.read_csv(packages_csv, usecols=["route_id", "stop_id", "service_s"])
    multi = P.groupby(["route_id", "stop_id"])["service_s"].agg(["size", "nunique"])
    multi = multi[multi["size"] > 1]
    if multi.empty:
        return "cannot tell (no stop has more than one package)"
    same = 100 * (multi["nunique"] == 1).mean()
    if service_is_per_stop(P):
        return (
            f"repeated per stop: at {same:.1f}% of stops with several packages every package "
            "has the same value, so it is one stop-level time; the generator uses the per-stop "
            "value (`service_max_s`), not the sum"
        )
    return (
        f"per package: only {same:.1f}% of multi-package stops repeat one value, so a stop's "
        "service time is the sum over its packages; the generator uses `service_sum_s`"
    )


def fill(doc: str, summary: dict, unit_answer: str) -> str:
    n, total = summary["n_routes"], summary.get("n_routes_in_file")
    scope = "all" if total is None or n >= total else "subset"
    total_txt = "?" if total is None else f"{total:,}"
    mb = summary.get("download_mb")
    doc = re.sub(
        r"^- Download size of what we used: .*$",
        f"- Download size of what we used: {mb if mb is not None else 'n/a'} MB · "
        f"Routes profiled: {n:,} of {total_txt} ({scope})",
        doc,
        flags=re.M,
    )
    for label, key, scale, dec in ROWS:
        q = summary.get(key, {})
        cells = " | ".join(fmt(q.get(k), dec, scale) for k in QUANTILES)
        doc = re.sub(rf"^\| {re.escape(label)} \|.*$", f"| {label} | {cells} |", doc, flags=re.M)
    doc = re.sub(
        r"^- Share of packages with a time window: .*$",
        f"- Share of packages with a time window: "
        f"{100 * summary['share_packages_with_tw']:.1f} %",
        doc,
        flags=re.M,
    )
    doc = re.sub(
        r"^- Share of stops with at least one windowed package: .*$",
        f"- Share of stops with at least one windowed package: "
        f"{100 * summary['share_stops_with_any_tw']:.1f} %",
        doc,
        flags=re.M,
    )
    doc = re.sub(
        r"^- Is service time per package or repeated per stop\? .*$",
        f"- Is service time per package or repeated per stop? {unit_answer}",
        doc,
        flags=re.M,
    )
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-dir", default="data/profile")
    ap.add_argument("--doc", default="docs/data_profile.md")
    a = ap.parse_args(argv)
    prof = Path(a.profile_dir)
    summary = json.loads((prof / "summary.json").read_text())
    doc_path = Path(a.doc)
    new = fill(doc_path.read_text(), summary, service_time_unit(prof / "packages.csv"))
    doc_path.write_text(new)
    left = new.count("___")
    print(f"filled {doc_path} from {prof}/summary.json ({left} blanks left)")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
