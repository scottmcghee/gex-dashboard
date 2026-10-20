"""Gamma-dependent Monte Carlo paths (small path counts keep these fast)."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from gex_dashboard import simulation as sim


@pytest.fixture
def profile():
    return pd.DataFrame({"strike": [95.0, 100.0, 105.0], "gex": [-1e6, 2e6, 1e6]})


class TestInterpolate:
    def test_exact_and_between(self, profile):
        out = sim.interpolate_gex(profile, np.array([95.0, 97.5, 100.0]))
        assert out.tolist() == pytest.approx([-1e6, 0.5e6, 2e6])

    def test_outside_range_is_zero(self, profile):
        assert sim.interpolate_gex(profile, np.array([90.0, 110.0])).tolist() == [0.0, 0.0]

    def test_empty_or_bad_profile(self):
        assert sim.interpolate_gex(pd.DataFrame(), np.array([1.0])).tolist() == [0.0]
        assert sim.interpolate_gex(pd.DataFrame({"strike": [1.0]}), np.array([1.0])).tolist() == [
            0.0
        ]

    def test_unsorted_and_single_strike(self):
        unsorted = pd.DataFrame({"strike": [105.0, 95.0], "gex": [1.0, 3.0]})
        assert sim.interpolate_gex(unsorted, np.array([100.0]))[0] == pytest.approx(2.0)
        single = pd.DataFrame({"strike": [100.0], "gex": [7.0]})
        assert sim.interpolate_gex(single, np.array([100.0, 101.0])).tolist() == [7.0, 0.0]


class TestSimulatePaths:
    def test_shape_and_start(self, profile):
        paths = sim.simulate_paths(100.0, profile, 0.2, n_steps=10, n_paths=20, seed=1)
        assert paths.shape == (20, 11)
        assert (paths[:, 0] == 100.0).all()

    def test_seed_reproducibility(self, profile):
        a = sim.simulate_paths(100.0, profile, 0.2, 10, n_paths=5, seed=7)
        b = sim.simulate_paths(100.0, profile, 0.2, 10, n_paths=5, seed=7)
        c = sim.simulate_paths(100.0, profile, 0.2, 10, n_paths=5, seed=8)
        assert np.array_equal(a, b)
        assert not np.array_equal(a, c)

    def test_positive_gex_narrows_negative_widens(self):
        flat = pd.DataFrame({"strike": [50.0, 150.0], "gex": [0.0, 0.0]})
        pos = pd.DataFrame({"strike": [50.0, 150.0], "gex": [1e6, 1e6]})
        neg = pd.DataFrame({"strike": [50.0, 150.0], "gex": [-1e6, -1e6]})
        kwargs = {"sigma_base": 0.3, "n_steps": 60, "n_paths": 300, "seed": 3, "dampening": 0.5}
        spread = {
            name: np.std(sim.simulate_paths(100.0, p, **kwargs)[:, -1])
            for name, p in (("flat", flat), ("pos", pos), ("neg", neg))
        }
        assert spread["pos"] < spread["flat"] < spread["neg"]

    def test_zero_dampening_ignores_gex(self, profile):
        empty = sim.simulate_paths(100.0, pd.DataFrame(), 0.2, 10, dampening=0.0, n_paths=5, seed=2)
        with_gex = sim.simulate_paths(100.0, profile, 0.2, 10, dampening=0.0, n_paths=5, seed=2)
        assert np.allclose(empty, with_gex)

    def test_all_zero_profile_runs(self):
        zero = pd.DataFrame({"strike": [99.0, 101.0], "gex": [0.0, 0.0]})
        assert sim.simulate_paths(100.0, zero, 0.2, 3, n_paths=2, seed=0).shape == (2, 4)

    def test_prices_stay_positive(self, profile):
        paths = sim.simulate_paths(100.0, profile, 0.5, 390, n_paths=50, seed=4)
        assert (paths > 0).all()


class TestBandsAndLabels:
    def test_bands_are_ordered(self):
        paths = np.random.default_rng(0).normal(100, 1, size=(200, 5))
        bands = sim.percentile_bands(paths)
        assert set(bands) == {5, 25, 50, 75, 95}
        assert (bands[5] <= bands[50]).all()
        assert (bands[50] <= bands[95]).all()
        assert sim.percentile_bands(paths, (10,))[10].shape == (5,)

    def test_time_labels(self):
        labels = sim.time_labels(3, datetime(2026, 10, 19, 15, 58), dt_minutes=1.0)
        assert labels == ["15:58", "15:59", "16:00", "16:01"]
        assert sim.time_labels(2, datetime(2026, 10, 19, 9, 30), dt_minutes=5) == [
            "09:30",
            "09:35",
            "09:40",
        ]

    def test_summary(self):
        paths = np.array([[100.0, 90.0], [100.0, 110.0], [100.0, 120.0], [100.0, 100.0]])
        summary = sim.summarize_paths(paths)
        assert summary.median == pytest.approx(105.0)
        assert not hasattr(summary, "share_above_spot")  # it read like a probability
        assert summary.p5 < summary.median < summary.p95
