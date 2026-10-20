"""Regime, alignment, index agreement and hedging-impact labels."""

from __future__ import annotations

import pytest

from gex_dashboard import profiles as pr

BANNED = ("fade", "momentum", "sit flat", "buy dips", "panic", "likely")


class TestGammaRegime:
    @pytest.mark.parametrize(
        ("gex", "vanna", "key"),
        [
            (1e9, None, "positive_gamma"),
            (0.0, -1e9, "positive_gamma"),
            (-1e9, None, "negative_gamma"),
            (-1e9, -1e8, "negative_gamma_vanna_aligned"),
            (-1e9, 1e8, "negative_gamma_vanna_opposed"),
            (-1e9, 0.0, "negative_gamma_vanna_opposed"),
        ],
    )
    def test_classification(self, gex, vanna, key):
        assert pr.classify_gamma_regime(gex, vanna).key == key

    def test_descriptions_give_no_advice(self):
        for regime in (
            pr.POSITIVE_GAMMA,
            pr.NEGATIVE_GAMMA,
            pr.NEGATIVE_GAMMA_VANNA_ALIGNED,
            pr.NEGATIVE_GAMMA_VANNA_OPPOSED,
        ):
            assert not any(word in regime.description.lower() for word in BANNED)

    def test_hedging_statements_name_the_assumption(self):
        for regime in (
            pr.POSITIVE_GAMMA,
            pr.NEGATIVE_GAMMA,
            pr.NEGATIVE_GAMMA_VANNA_ALIGNED,
            pr.NEGATIVE_GAMMA_VANNA_OPPOSED,
        ):
            assert "Under the assumed dealer positioning" in regime.description, regime.key
            assert "hedging" in regime.description, regime.key
            # Unavailable data is shown by the UI, not written into a regime's description.
            assert "not available" not in regime.description, regime.key

    def test_vanna_refinements_state_the_vanna_hedge(self):
        assert "lowers dealer delta" in pr.NEGATIVE_GAMMA_VANNA_ALIGNED.description
        assert "raises dealer delta" in pr.NEGATIVE_GAMMA_VANNA_OPPOSED.description


class TestGexVexAlignment:
    @pytest.mark.parametrize(
        ("gex", "vex", "state", "label"),
        [
            (1.0, 1.0, "aligned_positive", "+GEX / +VEX"),
            (-1.0, -1.0, "aligned_negative", "-GEX / -VEX"),
            (1.0, -1.0, "opposed_gex_positive", "+GEX / -VEX"),
            (-1.0, 1.0, "opposed_gex_negative", "-GEX / +VEX"),
            (0.0, 0.0, "aligned_positive", "+GEX / +VEX"),
        ],
    )
    def test_four_states(self, gex, vex, state, label):
        result = pr.classify_gex_vex_alignment(gex, vex)
        assert (result.state, result.label) == (state, label)
        assert not any(word in result.description.lower() for word in BANNED)


class TestIndexAgreement:
    POS, NEG = pr.POSITIVE_GAMMA, pr.NEGATIVE_GAMMA_VANNA_ALIGNED

    def test_aligned(self):
        result = pr.classify_index_agreement({"SPX": self.POS, "SPY": self.POS, "QQQ": self.POS})
        assert result.status == "aligned"
        assert result.shared_regime == "Positive gamma"

    def test_partial(self):
        result = pr.classify_index_agreement({"SPX": self.POS, "SPY": self.POS, "QQQ": self.NEG})
        assert result.status == "partial"
        assert result.shared_regime == "Positive gamma"
        assert "2/3" in result.label

    def test_divergent(self):
        result = pr.classify_index_agreement(
            {"SPX": self.POS, "SPY": self.NEG, "QQQ": pr.NEGATIVE_GAMMA_VANNA_OPPOSED}
        )
        assert result.status == "divergent"
        assert result.shared_regime is None

    def test_two_way_split_is_divergent(self):
        result = pr.classify_index_agreement({"SPX": self.POS, "SPY": self.NEG})
        assert result.status == "divergent"

    @pytest.mark.parametrize("regimes", [{"SPX": POS, "SPY": None, "QQQ": POS}, {"SPX": POS}, {}])
    def test_unavailable(self, regimes):
        result = pr.classify_index_agreement(regimes)
        assert result.status == "unavailable"

    def test_breakdown_keeps_per_symbol_labels(self):
        result = pr.classify_index_agreement({"SPX": self.POS, "SPY": None})
        assert result.breakdown == {"SPX": "Positive gamma", "SPY": None}


class TestHedgingImpact:
    def test_same_sign(self):
        assert pr.hedging_impact(1.0, 2.0, 3.0, 1.0).alignment == "same_sign"
        assert pr.hedging_impact(-1.0, -2.0, -3.0, 1.0).alignment == "same_sign"

    def test_mixed_and_neutral(self):
        assert pr.hedging_impact(1.0, -2.0, 3.0, 1.0).alignment == "mixed"
        assert pr.hedging_impact(0.0, 0.0, 0.0, 1.0).alignment == "neutral"
        assert pr.hedging_impact(0.0, 2.0, 0.0, 1.0).alignment == "same_sign"

    def test_charm_remaining_scales_with_session(self):
        assert pr.hedging_impact(1.0, 1.0, 100.0, 0.5).charm_remaining == pytest.approx(50.0)
        assert pr.hedging_impact(1.0, 1.0, 100.0, 0.0).charm_remaining == 0.0
        assert pr.hedging_impact(1.0, 1.0, 100.0, 1.0).charm_remaining == 100.0

    def test_fraction_clamped(self):
        assert pr.hedging_impact(1.0, 1.0, 100.0, 3.0).charm_remaining == 100.0
        assert pr.hedging_impact(1.0, 1.0, 100.0, -1.0).charm_remaining == 0.0

    def test_dominant_by_absolute_value(self):
        assert pr.hedging_impact(1.0, -5.0, 3.0, 1.0).dominant == "vanna"
        assert pr.hedging_impact(10.0, -5.0, 3.0, 1.0).dominant == "gamma"
        assert pr.hedging_impact(1.0, 2.0, -30.0, 1.0).dominant == "charm"
