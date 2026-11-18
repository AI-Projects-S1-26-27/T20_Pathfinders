"""R8: the one command that regenerates every result in the report.

    python -m src.eval.run_all                  # run (or resume) both sweeps, then tables, figures, verdicts
    python -m src.eval.run_all --workers 4      # more worker processes
    python -m src.eval.run_all --skip-run       # only redo tables, figures and verdicts from existing logs

Steps: (1) the launcher runs configs/sweeps/main.yaml and configs/sweeps/small_three_modes.yaml
(resumable: finished runs are skipped); (2) src.eval.aggregate turns each sweep into tables under
reports/r8/; (3) src.eval.plots draws F6, F7, F9 into reports/figures/; (4) src.eval.thresholds
evaluates the pre-registered criteria; (5) src.eval.report writes reports/r8/RESULTS.md; (6) a
manifest records what the numbers were produced from.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time

SWEEPS = [("main", "configs/sweeps/main.yaml"),
          ("small_three_modes", "configs/sweeps/small_three_modes.yaml")]
TRAFFIC_FILE = "data/raw/metr-la.h5"


def sh(args) -> None:
    print("$", " ".join(args), flush=True)
    subprocess.run([sys.executable, "-m", *args], check=True)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def manifest(out: str) -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:
        commit = "unknown"
    traffic = os.path.exists(TRAFFIC_FILE)
    m = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": commit,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "traffic_model": ("METR-LA multipliers (" + TRAFFIC_FILE + ", sha256 " + sha256(TRAFFIC_FILE) + ")"
                          if traffic else "NOT AVAILABLE: free-flow travel times plus disruption spikes only"),
        "predictor": "off in every sweep (predictor determinism not verified)",
        "sweeps": {name: sha256(path) for name, path in SWEEPS},
    }
    with open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description="Regenerate all results (R8).")
    ap.add_argument("--workers", type=int, help="worker processes for the launcher")
    ap.add_argument("--skip-run", action="store_true", help="do not run simulations, only analyse")
    args = ap.parse_args()
    out = "reports/r8"
    os.makedirs(out, exist_ok=True)
    if not args.skip_run:
        for _name, path in SWEEPS:
            cmd = ["src.sim.launcher", path]
            if args.workers:
                cmd += ["--workers", str(args.workers)]
            sh(cmd)
    sh(["src.eval.aggregate", SWEEPS[0][1], "--out", f"{out}/main",
        "--pair", "full:independent"])
    sh(["src.eval.aggregate", SWEEPS[1][1], "--out", f"{out}/small_three_modes",
        "--pair", "full:centralized", "--pair", "full:independent", "--pair", "independent:centralized"])
    sh(["src.eval.plots", "--main", f"{out}/main", "--small", f"{out}/small_three_modes",
        "--out", "reports/figures"])
    sh(["src.eval.thresholds", "--main", f"{out}/main", "--small", f"{out}/small_three_modes",
        "--out", f"{out}/thresholds.md"])
    m = manifest(out)
    sh(["src.eval.report", "--out", f"{out}/RESULTS.md"])
    print("traffic model:", m["traffic_model"])


if __name__ == "__main__":
    main()
