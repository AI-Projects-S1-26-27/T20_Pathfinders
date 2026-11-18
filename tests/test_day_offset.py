"""R6: optional day-per-seed offset."""
import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("torch")
pytestmark = pytest.mark.skipif(
    not os.path.exists("data/raw/metr-la.h5"),
    reason="METR-LA data is git-ignored; run locally",
)

from src.prediction.predictor import Predictor  # noqa: E402
from src.world.traffic_model import TrafficModel, weekday_offsets  # noqa: E402


def test_default_unchanged():
    a = TrafficModel.from_graph(seed=5)
    b = TrafficModel.from_graph(seed=5, day_offset=0)
    assert np.array_equal(a.mult, b.mult)


def test_offset_rolls_whole_days():
    a = TrafficModel.from_graph(seed=5)
    b = TrafficModel.from_graph(seed=5, day_offset=3)
    assert np.array_equal(b.mult[:, :288], a.mult[:, 864:1152])



def test_predictor_clock_follows_offset():
    base = Predictor(TrafficModel.from_graph())
    p = Predictor(TrafficModel.from_graph(day_offset=3))
    assert p.t0 - base.t0 == pd.Timedelta(days=3)
    v = p.predict(0, 9 * 3600, 15)
    assert 1.0 <= v <= 6.0


def test_range_check_and_day_for_seed():
    from src.world.traffic_model import day_for_seed
    with pytest.raises(ValueError):
        TrafficModel.from_graph(day_offset=-1)
    with pytest.raises(ValueError):
        TrafficModel.from_graph(day_offset=23)
    offs = weekday_offsets()
    d0 = pd.Timestamp("2012-06-05").dayofweek
    assert len(offs) == sum((d0 + d) % 7 < 5 for d in range(23))
    for seed in range(40):
        d = day_for_seed(seed)
        assert d == day_for_seed(seed) and (d0 + d) % 7 < 5


def test_weekday_feature_matches_clock():
    for off in (0, 3):
        p = Predictor(TrafficModel.from_graph(day_offset=off))
        for step in (0, 150, 400):
            ts = p.t0 + pd.Timedelta(minutes=5 * step)
            assert round(p._features(step)[-1, 2] * 6) == ts.dayofweek


def test_table_days_matches_data():
    from src.world.traffic_model import TABLE_DAYS
    assert TrafficModel.from_graph().n_days == TABLE_DAYS
