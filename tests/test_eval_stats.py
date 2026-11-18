"""R8 W3: bootstrap and paired statistics."""
import math

import pytest

from src.eval.stats import bootstrap_ci, intervals_overlap, paired_diff_ci, paired_ratio_ci


def test_bootstrap_is_seeded():
    x = [1.0, 2.0, 4.0, 8.0, 3.0, 5.0]
    assert bootstrap_ci(x, n_boot=2000, seed=3) == bootstrap_ci(x, n_boot=2000, seed=3)


def test_bootstrap_mean_is_the_plain_mean_and_ci_brackets_it():
    x = [1.0, 2.0, 4.0, 8.0, 3.0, 5.0]
    mean, lo, hi = bootstrap_ci(x, n_boot=2000)
    assert mean == pytest.approx(sum(x) / len(x))
    assert lo < mean < hi
    assert min(x) <= lo and hi <= max(x)


def test_constant_data_has_a_zero_width_interval():
    assert bootstrap_ci([4.0] * 5, n_boot=500) == (4.0, 4.0, 4.0)


def test_too_few_values_give_no_interval():
    mean, lo, hi = bootstrap_ci([7.0])
    assert mean == 7.0 and math.isnan(lo) and math.isnan(hi)
    assert all(math.isnan(v) for v in bootstrap_ci([]))


def test_nan_values_are_ignored():
    assert bootstrap_ci([1.0, float("nan"), 3.0], n_boot=500)[0] == pytest.approx(2.0)


def test_paired_difference_removes_the_shared_run_effect():
    base = [10.0, 50.0, 90.0, 30.0]                  # runs differ a lot from each other
    a = [b + 2.0 for b in base]                       # but a is always exactly 2 above b
    assert paired_diff_ci(a, base, n_boot=500) == (2.0, 2.0, 2.0)
    # unpaired intervals for the same data overlap heavily, which is why we pair by run
    assert intervals_overlap(*bootstrap_ci(a, n_boot=500)[1:], *bootstrap_ci(base, n_boot=500)[1:])


def test_paired_ratio_is_the_relative_difference():
    b = [100.0, 200.0, 400.0]
    a = [110.0, 220.0, 440.0]
    mean, lo, hi = paired_ratio_ci(a, b, n_boot=500)
    assert (mean, lo, hi) == (pytest.approx(0.10), pytest.approx(0.10), pytest.approx(0.10))


def test_paired_ratio_skips_zero_denominators():
    mean, _, _ = paired_ratio_ci([1.0, 2.0, 3.0], [0.0, 2.0, 2.0], n_boot=200)
    assert mean == pytest.approx(0.25)               # pairs (2/2 - 1) = 0 and (3/2 - 1) = 0.5


def test_paired_needs_equal_lengths():
    with pytest.raises(ValueError):
        paired_diff_ci([1.0, 2.0], [1.0])


def test_interval_overlap():
    assert intervals_overlap(0, 2, 1, 3)
    assert not intervals_overlap(0, 1, 2, 3)
    assert intervals_overlap(0, 1, float("nan"), float("nan"))
