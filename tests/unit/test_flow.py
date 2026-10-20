"""Customer flow aggregation and volume spikes."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from gex_dashboard import flow
from gex_dashboard.clock import FixedClock
from gex_dashboard.exposures import dollar_delta, dollar_gamma
from gex_dashboard.providers import synthetic
from gex_dashboard.providers.synthetic import SyntheticProvider
from tests.unit.conftest import make_chain

E1, E2, E3 = date(2026, 10, 23), date(2026, 10, 30), date(2026, 11, 20)


@pytest.fixture
def chain():
    return make_chain(
        [
            {
                "strike": 100.0,
                "right": "C",
                "expiration": E1,
                "delta": 0.5,
                "gamma": 0.05,
                "volume_buy": 300,
                "volume_sell": 100,
                "premium_buy": 900.0,
                "premium_sell": 200.0,
            },
            {
                "strike": 95.0,
                "right": "P",
                "expiration": E1,
                "delta": -0.3,
                "gamma": 0.04,
                "volume_buy": 500,
                "volume_sell": 100,
                "premium_buy": 1000.0,
                "premium_sell": 100.0,
            },
            {
                "strike": 100.0,
                "right": "C",
                "expiration": E2,
                "delta": 0.5,
                "gamma": 0.02,
                "volume_buy": 10,
                "volume_sell": 60,
                "premium_buy": 50.0,
                "premium_sell": 400.0,
            },
            {
                "strike": 105.0,
                "right": "C",
                "expiration": E3,
                "delta": 0.2,
                "gamma": 0.01,
                "volume_buy": 1,
                "volume_sell": 0,
                "premium_buy": 1.0,
                "premium_sell": 0.0,
            },
        ]
    )


class TestNetFlow:
    def test_columns_and_units(self, chain):
        df = flow.net_flow(chain, 100.0)
        first = df.iloc[0]
        assert first["net_volume"] == 200
        assert first["net_premium"] == 700.0
        assert first["net_delta"] == pytest.approx(dollar_delta(0.5, 200, 100.0))
        assert first["net_gamma"] == pytest.approx(dollar_gamma(0.05, 200, 100.0))

    def test_bought_puts_are_negative_delta(self, chain):
        put = flow.net_flow(chain, 100.0).iloc[1]
        assert put["net_delta"] < 0
        assert put["net_gamma"] > 0


class TestByExpiration:
    def test_split_by_right(self, chain):
        out = flow.flow_by_expiration(chain, 100.0, "volume")
        assert list(out["expiration"]) == [E1, E2, E3]
        assert out.iloc[0][["calls", "puts", "total"]].tolist() == [200, 400, 600]
        assert out.iloc[1][["calls", "puts"]].tolist() == [-50, 0]

    def test_max_expirations(self, chain):
        assert len(flow.flow_by_expiration(chain, 100.0, "premium", max_expirations=2)) == 2

    def test_matrix(self, chain):
        grid = flow.flow_matrix(chain, 100.0, "volume")
        assert list(grid.columns) == [E1, E2, E3]
        assert grid.loc[100.0, E1] == 200
        assert grid.loc[95.0, E2] == 0

    def test_summary(self, chain):
        summary = flow.flow_summary(chain, 100.0)
        assert list(summary.columns) == ["expiration", *flow.FLOW_METRICS]
        assert summary.loc[0, "volume"] == 600
        assert summary.loc[0, "premium"] == 1600.0

    def test_without_flow_columns(self, chain):
        bare = chain.drop(columns=["volume_buy"])
        assert flow.flow_by_expiration(bare, 100.0, "gamma").empty
        assert flow.flow_matrix(bare, 100.0, "gamma").empty
        assert flow.flow_summary(bare, 100.0).empty
        assert flow.net_delta_flow(bare, 100.0) is None


class TestNetDeltaFlow:
    def test_split_and_total(self, chain):
        result = flow.net_delta_flow(chain, 100.0)
        calls = (
            dollar_delta(0.5, 200, 100.0)
            + dollar_delta(0.5, -50, 100.0)
            + dollar_delta(0.2, 1, 100.0)
        )
        puts = dollar_delta(-0.3, 400, 100.0)
        assert result.calls == pytest.approx(calls)
        assert result.puts == pytest.approx(puts)
        assert result.net == pytest.approx(calls + puts)


class TestVolumeSpikes:
    @staticmethod
    def ladder(volumes, oi=99, right="C", expiration=E1):
        """One expiration and right: strikes 0, 1, 2, ... with the given volumes."""
        return make_chain(
            [
                {
                    "strike": float(k),
                    "right": right,
                    "expiration": expiration,
                    "volume": v,
                    "open_interest": oi,
                }
                for k, v in enumerate(volumes)
            ]
        )

    def test_threshold_and_order(self):
        chain = self.ladder([10] * 8 + [50] + [10] * 3 + [100] + [10] * 8)
        spikes = flow.volume_spikes(chain, threshold=3.0)
        assert spikes["strike"].tolist() == [12.0, 8.0]  # most unusual first
        assert spikes["vol_oi_ratio"].iloc[0] == pytest.approx(1.0)
        assert spikes["vs_neighbours"].tolist() == pytest.approx([10.0, 5.0])
        assert flow.volume_spikes(chain, threshold=6.0)["strike"].tolist() == [12.0]

    def test_baseline_is_relative_to_open_interest(self):
        # Twice the volume on twice the open interest is not unusual.
        chain = self.ladder([10] * 11)
        chain.loc[5, ["volume", "open_interest"]] = [20, 199]
        assert flow.volume_spikes(chain, threshold=1.5).empty

    def test_baseline_is_per_expiration_and_right(self):
        quiet = self.ladder([10] * 11)
        busy_puts = self.ladder([100] * 11, right="P")
        busy_later = self.ladder([100] * 11, expiration=E2)
        assert flow.volume_spikes(pd.concat([quiet, busy_puts, busy_later])).empty

    def test_untraded_contracts_are_left_out_of_the_baseline(self):
        chain = self.ladder([0, 0, 0, 0, 10, 10, 40, 10, 10, 0, 0, 0])
        assert flow.volume_spikes(chain, threshold=3.0)["strike"].tolist() == [6.0]
        assert flow.volume_spikes(self.ladder([0, 0, 50, 0, 0])).empty  # too few neighbours

    def test_fewer_than_min_neighbours_traded_gives_no_flag(self):
        # One fewer traded contract than MIN_NEIGHBOURS leaves no baseline, however large the
        # spike; one more quiet contract in the window gives a baseline and a flag.
        quiet = [10] * (flow.MIN_NEIGHBOURS - 2)
        sparse = self.ladder([0] * 4 + [*quiet, 100] + [0] * 4)
        assert flow.volume_spikes(sparse, threshold=1.5, min_volume=1).empty
        enough = self.ladder([0] * 4 + [*quiet, 10, 100] + [0] * 4)
        flagged = flow.volume_spikes(enough, threshold=1.5, min_volume=1)
        assert flagged["volume"].tolist() == [100]

    def test_a_few_lots_are_not_a_spike(self):
        chain = self.ladder([1] * 5 + [9] + [1] * 5)
        assert flow.volume_spikes(chain).empty
        assert flow.volume_spikes(chain, min_volume=1)["strike"].tolist() == [5.0]

    def test_nothing_traded(self):
        assert flow.volume_spikes(self.ladder([0] * 20)).empty

    def test_includes_flow_split(self, chain):
        assert "volume_buy" in flow.volume_spikes(chain, threshold=0.0)

    def test_without_flow(self):
        chain = make_chain([{"strike": 1.0, "right": "C", "volume": 5}]).drop(
            columns=["volume_buy", "volume_sell", "premium_buy", "premium_sell"]
        )
        assert list(flow.volume_spikes(chain, 0.0).columns) == list(flow.SPIKE_COLUMNS)

    def test_empty(self):
        assert flow.volume_spikes(pd.DataFrame()).empty


class TestVolumeSpikesOnTheSyntheticMarket:
    NOW = datetime.fromisoformat("2026-10-19T13:00:00-04:00")

    @staticmethod
    def keys(df):
        return set(zip(df["expiration"], df["strike"], df["right"], strict=True))

    @pytest.mark.parametrize("symbol", ["SPY", "SPX", "QQQ"])
    def test_widening_the_strike_range_keeps_the_near_the_money_flags(self, symbol):
        provider = SyntheticProvider(FixedClock(self.NOW))
        spot = provider.spot(symbol)

        def near_money(strike_range):
            spikes = flow.volume_spikes(provider.chain(symbol, strike_range=strike_range))
            # at least NEIGHBOUR_STRIKES listed strikes inside the narrower range
            return self.keys(spikes[(spikes["strike"] / spot - 1).abs() <= 0.09])

        narrow, wide = near_money(0.10), near_money(0.25)
        assert narrow, "nothing flagged near the money"
        assert narrow == wide

    @pytest.mark.parametrize("symbol", ["SPY", "SPX", "QQQ"])
    def test_flags_the_seeded_spikes(self, symbol, monkeypatch):
        provider = SyntheticProvider(FixedClock(self.NOW))
        chain = provider.chain(symbol)
        monkeypatch.setattr(synthetic, "SPIKE_MULTIPLIER", 1.0)
        unspiked = provider.chain(symbol)  # same seeds, so only the spikes differ
        spiked = chain["volume"] != unspiked["volume"]
        seeded = self.keys(chain[spiked & (chain["volume"] >= flow.MIN_SPIKE_VOLUME)])
        flagged = self.keys(flow.volume_spikes(chain))
        assert flagged <= seeded, flagged - seeded  # nothing but seeded spikes
        assert len(flagged) >= 0.8 * len(seeded), seeded - flagged


def test_summary_matches_the_per_metric_totals(chain):
    summary = flow.flow_summary(chain, 100.0)
    for metric in flow.FLOW_METRICS:
        totals = flow.flow_by_expiration(chain, 100.0, metric)
        assert summary[metric].tolist() == pytest.approx(totals["total"].tolist()), metric
    assert summary["expiration"].tolist() == totals["expiration"].tolist()
