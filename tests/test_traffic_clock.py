"""R6's traffic clock as the simulator uses it (was TODO(R6) in src/sim/world.py).

The simulator's t is absolute seconds from midnight (start_time_s + env.now).
R6's TrafficModel table has one column per 5-minute step: column s is sim time
s x 300 s, and reads METR-LA one hour earlier (LA rush hours shifted +1 h to
approximate Hyderabad). The Predictor must use the same clock for its time
features. Needs data/raw/metr-la.h5, torch and h5py: skipped without them (CI).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "raw" / "metr-la.h5").exists(),
                                reason="R6's METR-LA data not present")


@pytest.fixture(scope="module", params=[0, 1], ids=["day0", "day1"])
def world(request):
    pytest.importorskip("torch")
    pytest.importorskip("h5py")
    from src.sim.world import World
    w = World.from_defaults(day_offset=request.param)
    assert w.traffic_model is not None and w.predictor is not None
    return w


@pytest.fixture(scope="module")
def test_index():
    pytest.importorskip("h5py")
    from src.prediction.data_prep import prepare_ratio
    return prepare_ratio()[3].index


def test_table_steps_are_five_minutes_without_gaps(test_index):
    """Column position = time only if the source index has no gaps."""
    from src.world.traffic_model import STEP_S
    assert set(np.diff(test_index.asi8) // 10**9) == {STEP_S}


def test_world_reads_the_table_at_t_over_300(world):
    from src.world.traffic_model import STEP_S
    tm = world.traffic_model
    (u, v), e = next(iter(tm.edge_index.items()))
    for t in (0.0, 21600.0, 8 * 3600 + 299.0, 86399.0):
        assert world.traffic_multiplier(int(u), int(v), t) == tm.mult[e, int(t // STEP_S)]


def test_step_zero_is_midnight_reading_la_one_hour_earlier(world, test_index):
    """Predictor time of step s == LA time of table column s (+1 h shift, day offset)."""
    from src.world.traffic_model import GLOBAL_SHIFT_STEPS, SIM_START
    tm, pr = world.traffic_model, world.predictor
    start = test_index.get_loc(pd.Timestamp(SIM_START))
    for step in (0, 96, 287):
        la = test_index[start - GLOBAL_SHIFT_STEPS + step + 288 * tm.day_offset]
        assert pr.t0 + pd.Timedelta(minutes=5 * step) == la
    assert (pr.t0 + pd.Timedelta(hours=1)).time() == pd.Timestamp("00:00").time()


def test_morning_rush_is_in_the_simulated_morning(world):
    """Strong (motorway/trunk/primary) roads: 09:00 sim much slower than 03:00."""
    tm = world.traffic_model
    strong = np.isin(np.array(tm.road_classes), ["motorway", "trunk", "primary"])
    night = tm.mult[strong, 3 * 12:4 * 12].mean()
    rush = tm.mult[strong, 9 * 12:10 * 12].mean()
    assert night < 1.3 and rush > 2.0


def test_forecast_tracks_the_table_at_the_same_time(world):
    """A 15-min forecast at 08:00 is close to the table at 08:15, not to 03:15:
    a clock off by hours would make it track the wrong time of day."""
    from src.world.traffic_model import STEP_S
    tm, pr = world.traffic_model, world.predictor
    strong = np.flatnonzero(np.isin(np.array(tm.road_classes), ["motorway", "trunk", "primary"]))
    t = 8 * 3600.0
    pred = np.array([pr.predict(i, t, 15) for i in strong])
    same = np.abs(pred - tm.mult[strong, int(t // STEP_S) + 3]).mean()
    other = np.abs(pred - tm.mult[strong, int(3 * 3600 // STEP_S) + 3]).mean()
    assert same < 0.6 * other
