"""R8 statistics helpers (plan W3): mean, 95% bootstrap CI, paired comparison by seed.

All resampling is seeded, so the same inputs always give the same interval.

* ``bootstrap_ci(x)``      mean of x with a percentile bootstrap CI (resample the runs).
* ``paired_diff_ci(a, b)`` a and b are matched run by run (same scenario, seed and rate); the CI is
                           for the mean of (a_i - b_i), resampling the pairs.
* ``paired_ratio_ci(a, b)`` the same for the mean of (a_i / b_i) - 1, the relative difference.
* ``intervals_overlap``    True if two (lo, hi) intervals share a point.
"""
import math
from typing import Sequence, Tuple

import numpy as np

N_BOOT = 10_000
ALPHA = 0.05


def _clean(x: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(x), dtype=float)
    return arr[~np.isnan(arr)]


def bootstrap_ci(x: Sequence[float], n_boot: int = N_BOOT, alpha: float = ALPHA,
                 seed: int = 0) -> Tuple[float, float, float]:
    """(mean, lo, hi). With fewer than 2 values the interval is (nan, nan)."""
    arr = _clean(x)
    if arr.size == 0:
        return (math.nan, math.nan, math.nan)
    mean = float(arr.mean())
    if arr.size < 2:
        return (mean, math.nan, math.nan)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n_boot, arr.size))
    means = arr[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return (mean, float(lo), float(hi))


def paired_diff_ci(a: Sequence[float], b: Sequence[float], n_boot: int = N_BOOT,
                   alpha: float = ALPHA, seed: int = 0) -> Tuple[float, float, float]:
    """(mean(a - b), lo, hi) over matched pairs; pairs with a missing value are dropped."""
    a_arr = np.asarray(list(a), dtype=float)
    b_arr = np.asarray(list(b), dtype=float)
    if a_arr.shape != b_arr.shape:
        raise ValueError(f"paired samples must have the same length, got {a_arr.size} and {b_arr.size}")
    keep = ~(np.isnan(a_arr) | np.isnan(b_arr))
    return bootstrap_ci(a_arr[keep] - b_arr[keep], n_boot, alpha, seed)


def paired_ratio_ci(a: Sequence[float], b: Sequence[float], n_boot: int = N_BOOT,
                    alpha: float = ALPHA, seed: int = 0) -> Tuple[float, float, float]:
    """(mean(a / b - 1), lo, hi) over matched pairs; pairs with b == 0 or a missing value are dropped."""
    a_arr = np.asarray(list(a), dtype=float)
    b_arr = np.asarray(list(b), dtype=float)
    if a_arr.shape != b_arr.shape:
        raise ValueError(f"paired samples must have the same length, got {a_arr.size} and {b_arr.size}")
    keep = ~(np.isnan(a_arr) | np.isnan(b_arr)) & (b_arr != 0)
    return bootstrap_ci(a_arr[keep] / b_arr[keep] - 1.0, n_boot, alpha, seed)


def intervals_overlap(lo1: float, hi1: float, lo2: float, hi2: float) -> bool:
    if any(math.isnan(v) for v in (lo1, hi1, lo2, hi2)):
        return True            # no interval, so no claim that they differ
    return not (hi1 < lo2 or hi2 < lo1)
