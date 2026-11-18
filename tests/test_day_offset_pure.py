"""R6: pure day-offset functions. No torch or METR-LA needed, so CI runs these."""
import pandas as pd

from src.world.traffic_model import (BAD_DAYS, TABLE_DAYS, day_for_seed,
                                     weekday_offsets)

D0 = pd.Timestamp("2012-06-05").dayofweek


def test_weekday_offsets_only_weekdays():
    offs = weekday_offsets()
    assert offs and all((D0 + d) % 7 < 5 for d in offs)


def test_weekday_offsets_count():
    assert len(weekday_offsets()) == sum((D0 + d) % 7 < 5 for d in range(TABLE_DAYS))


def test_day_for_seed_deterministic_weekday():
    for seed in range(60):
        d = day_for_seed(seed)
        assert d == day_for_seed(seed) and (D0 + d) % 7 < 5 and 0 <= d < TABLE_DAYS


def test_bad_day_never_chosen():
    assert all(day_for_seed(s) not in BAD_DAYS for s in range(200))
