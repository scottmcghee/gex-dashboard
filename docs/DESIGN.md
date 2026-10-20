# Design

This document defines what the dashboard computes, with the formulas, and
records the main decisions as short notes, each with its reason. Module names
are relative to `src/gex_dashboard/`. Test names in brackets verify the
statement before them.

## Terms

- **Chain.** One row per option contract, in the canonical columns of
  `providers/base.py`. Greeks are per share: `delta` per $1, `gamma` per $1
  of spot, `vega` and `vanna` per volatility point (σ + 0.01), `theta` and
  `charm` per trading day.
- **S** is spot, **OI** open interest in contracts, and **100** the contract
  multiplier.
- **Exposure.** A greek scaled to dollars and multiplied by contracts. With
  OI it is a position estimate; with net traded contracts it is flow.
- **GEX** is gamma exposure, **VEX** vanna exposure.

## Dealer sign convention

**Decision.** Exposures are reported from the market maker's side, assuming
customers sell calls and buy puts, so dealers are **long every call (+1) and
short every put (−1)** in open interest. One constant,
`exposures.DEALER_SIGN = {"C": 1.0, "P": -1.0}`, decides this; every signed
exposure is `DEALER_SIGN[right] × dollar_<greek>(...)`.
[`test_single_constant`, `test_puts_are_negative_calls_positive`]

**Reason.** A chain carries open interest but not who holds it. The
long-calls, short-puts convention is the common simplification in published
GEX work, and keeping it in one constant means a different assumption (or
real positioning data) changes one place. The UI's Assumptions panel states
it, and every label is worded as a description under it.

**Consequences.**
- Delta, gamma, vanna and charm exposures are dealer-signed. Vega exposure is
  **gross** (unsigned): it is used only to find where volatility sensitivity
  concentrates (the vega peaks), which a sign would distort.
  [`test_vanna_and_charm_follow_the_sign_vega_does_not`]
- Customer flow (`flow.py`) is reported from the **customer** side and is
  not dealer-signed: positive means customers bought more than they sold.

## Dollar units

All conversions are in `exposures.py`:

| Exposure | Function | Formula | One unit means |
|---|---|---|---|
| Delta | `dollar_delta` | delta × contracts × 100 × S | dollars of underlying the position is equivalent to |
| Gamma (GEX) | `dollar_gamma` | gamma × contracts × 100 × S² × 0.01 | dollars of delta that change for a 1% move in spot |
| Vanna | `dollar_vanna` | vanna × contracts × 100 × S | dollars of delta that change when IV rises 1 vol point |
| Charm | `dollar_charm` | charm × contracts × 100 × S | dollars of delta that change over 1 trading day |
| Vega | `dollar_vega` | vega × contracts × 100 | dollars of option value that change when IV rises 1 vol point |

Gamma's S² comes from two factors of S: a 1% move is ΔS = 0.01 × S, which
changes delta per share by gamma × 0.01 × S, and that delta change is worth
× 100 × S dollars per contract. GEX therefore grows with S², and SPX and SPY
exposures are comparable in dollars, not in contracts.
[`test_gamma_is_dollars_per_one_percent_move`,
`test_gamma_matches_a_one_percent_move`, `test_vanna_matches_a_vol_point`,
`test_charm_matches_one_trading_day`, `test_gamma_scales_with_spot_squared`]

**Missing greeks.** A contract whose greek is NaN gets a NaN exposure for
that greek. Sums leave such contracts out rather than counting them as zero,
and a metric is unavailable only when no contract has it.
[`test_partial_nan_gamma_is_left_out_of_sums`, `test_all_nan_is_unavailable`]

### Why units are converted in one place

**Decision.** Providers return per-share greeks in the documented chain units
and never pre-compute exposures. `exposures.contract_exposures` converts
every contract once, and every profile, level, chart and card reads its
columns.

**Reason.** Per-share greeks and dollar exposures differ by a factor of
about 100 × S (tens of thousands for SPY), so a conversion applied on some
paths and not on others would change a chart's scale silently, and a ratio
of two exposures would mix units. With one conversion, every number on
screen has one documented unit, and the unit tests pin each formula. Because
every vendor "exposure" is a per-contract greek times contracts (see
[the ConvexValue check](#convexvalue-units)), the core can compute all of
them from a plain chain, and a provider only has to map fields.

## Gamma flip

**Definition.** The price at which net dealer GEX, **repriced at hypothetical
spot prices**, crosses zero from negative below to positive above.

`exposures.gex_curve` evaluates net GEX at 201 prices across spot ±10%. At
each price P it recomputes every contract's gamma with Black-Scholes-Merton
from the contract's own IV and its trading-time years to expiry at `now`
(rate `GEX_RISK_FREE_RATE`, no dividend yield), dealer-signs it, weights it
by OI and converts with `dollar_gamma` at P. Calls and puts with the same
strike, expiry and IV share a gamma and are priced once. Contracts without a
positive IV or OI, or already expired, contribute nothing.
[`test_at_spot_equals_the_chain_gex`, `test_reprices_at_other_prices`,
`test_skips_unpriceable_contracts`]

`levels.gamma_flip` finds the rising zero crossings on that curve and
interpolates each linearly between grid prices, so the flip's resolution is
one grid step (0.1% of spot). The sign of the curve's own value at spot
picks the side: when it is negative the flip is the first crossing above
spot, otherwise the nearest one at or below spot. So spot is below the flip
exactly when repriced net GEX at spot is negative. A curve that is exactly 0
at spot after a negative grid point counts as zero or positive, so the flip
is spot itself.
[`test_interpolates_the_crossing_below_spot_when_positive`,
`test_curve_exactly_zero_at_spot_after_a_negative_point_flips_at_spot`,
`test_crossing_above_spot_when_negative`,
`test_picks_the_crossing_on_the_side_set_by_the_sign_at_spot`,
`test_gamma_flip_is_a_rising_zero_of_the_repriced_curve`]

At spot the curve equals net GEX from the chain's own gamma whenever that
gamma is the Black-Scholes-Merton gamma at the contract's IV, as in the
synthetic chain [`test_at_spot_equals_the_chain_gex`]. A vendor whose gamma
follows other conventions (rate, dividends, day count) can disagree with the
curve when net GEX is near zero. The curve then decides the side, so the
flip is always a crossing of the curve it was found on, and the disagreement
is logged. [`test_a_disagreeing_net_gex_is_logged_and_the_curve_decides`,
`test_a_disagreeing_vendor_gamma_leaves_the_side_to_the_curve`]

When there is no flip, the UI says why, in one of two ways:
- **No usable IV to reprice.** No contract in view has open interest, a
  positive IV and time left to expiry, so the curve is zero everywhere.
- **No crossing on that side.** The curve does not turn from negative to
  positive above spot (when repriced net GEX at spot is negative) or at or
  below spot (when it is zero or positive), within the ±10% grid.

[`TestGammaFlipNote`, `test_all_calls_chain_has_no_put_walls`,
`test_zero_open_interest_shows_n_a`]

Like every figure, the flip covers only the strikes within the sidebar's
strike range: contracts outside it are not on the curve.

**Decision: reprice rather than read the flip off the strike profile.** A
sign change in per-strike GEX, or in GEX summed in strike order, is not used
as the flip.

**Reason.** Per-strike GEX is measured at today's spot, so a sign change
across strikes says where call OI gives way to put OI, not what net GEX
would be if spot moved there: as spot moves, every contract's gamma changes
with its moneyness and time to expiry. A strike rule also returns only listed
strikes, depends on the order of summation, and needs a rule for choosing
among several sign changes. Repricing answers the question the label asks
("where does net dealer gamma change sign as the price moves?"), needs no
threshold, agrees with net GEX at spot by construction (the curve at spot is
net GEX), and is cheap: a few tens of milliseconds for thousands of
contracts (see [REQUIREMENTS.md](REQUIREMENTS.md#performance)).

## Walls, vanna flip and vega peaks

- **Call walls**: up to three strikes with the largest positive call GEX.
  **Put walls**: up to three with the most negative put GEX. A strike with
  no call (or put) GEX is never a wall, so a chain can have fewer.
- **Vanna flip**: the strike nearest spot where per-strike dealer vanna
  exposure changes sign. Strikes with zero or missing vanna are skipped, so a
  zero at the crossing does not hide it. Unlike the gamma flip it is a
  strike, not a repriced price.
- **Vega peaks**: the strikes with the most gross vega strictly above and
  strictly below spot.
- **Gamma ratio**: |call GEX| / (|call GEX| + |put GEX|).

## Expected move

**Definition.** One standard deviation of price to the front expiration's
close, in dollars:

```text
expected move = S × σ_ATM × √T
```

σ_ATM is the mean IV of the contracts (call and put) at the strike nearest
spot that has a valid IV, in the front expiration: the nearest expiration
whose close is still ahead (see [Expired contracts](#expired-contracts)). T
is trading-time years to that expiration's 16:00 close. Charts that span
several expirations (the heatmaps) label the ±1σ lines with the front
expiration's date, since the move belongs to it alone, on a semi-opaque
box so the labels read over cells of any color. The move is recomputed
from the chain on every view, never cached separately, so it always
matches the chain on screen. It is a 1σ range under a normal
approximation, not a bound.

## Expired contracts

**Decision.** `analysis.analyze_chain` first drops every contract whose
expiration close (16:00 New York time) is at or before `now`, and the front
expiration is the nearest one left. The sidebar does not offer an expiration
that has closed.

**Reason.** A live source can still list today's expiration after the close.
Kept, its contracts would count in net GEX, the walls and the profiles from
their last greeks, while the repriced curve (which needs time to expiry)
leaves them out, so net GEX and the flip would describe different books; as
the front expiration it would also leave no expected move. Dropped, net GEX,
the walls, the curve, the expected move and ATM IV all describe the same
contracts. [`test_expirations_closed_at_now_are_dropped`]

## Trading time

**Decision.** Time to expiry is counted in **trading minutes**: only minutes
inside the regular session (09:30–16:00 New York time, Monday to Friday)
count, an option expires at the 16:00 close of its expiration date, and a
year is 252 × 390 trading minutes. `clock.years_to_expiry` gives T for the
Black-Scholes repricing, the expected move and the synthetic chain.

**Reason.** For a same-day option, calendar time overstates T by including
the night and makes T jump at midnight instead of reaching zero at the
close. In trading time a 0DTE option's T falls steadily through the session
and is zero at 16:00, and a weekend adds nothing. The simulation steps in the
same unit, so its volatility and the chain's IV are on one clock. The cost:
variance outside the session (overnight gaps) is treated as zero, and
exchange holidays count as sessions, which slightly overstates T across a
holiday.

**Injected clock.** Every time-dependent calculation takes `now` from a
`Clock` (see [ARCHITECTURE.md](ARCHITECTURE.md#the-clock)), and
`build_market_view` reads it once per view. Tests pin the time, and the demo
replays a session at any hour.

## ConvexValue units

**Decision.** The adapter maps vendor fields onto the canonical units with
two scale factors: vega and vanna × 0.01 (per 1.0 of volatility → per vol
point), theta and charm ÷ 252 (per year → per trading day). The vendor sends
vanna and charm only multiplied by OI (`vannaxoi`, `charmxoi`), so the adapter
divides by OI and leaves them NaN where OI is 0.

**How it was checked.** The units were checked against the live API on
2026-10-19 with a few read-only requests on SPY:

- `gxoi / (gamma × oi)` had a median of 1.000, and so did `dxoi / (delta ×
  oi)` and `vxoi / (vega × oi)`. So `gxoi` is per-share gamma × OI, with no
  dollar scaling, and multiplying by 100 × S (and by S × 0.01 for a 1% move)
  is a conversion, not double counting.
- Against Black-Scholes values computed from the vendor's own IV (155
  contracts, 2–6 weeks out), the vendor's delta and gamma match the canonical
  units, its vega and vanna are per 1.0 of volatility (ratios 1.01 and 0.98
  after scaling), and its theta and charm are per year (ratios 0.91 and 0.92;
  the gap is consistent with day-count and rate conventions).

The adapter's tests pin the mapping against a fake `convexlib`; no test calls
the API.

## Labels from the net exposures

`profiles.py` turns signs into labels. They describe the computed exposures
under the sign convention; none is a forecast.

- **Gamma regime.** Positive gamma when net GEX ≥ 0. Otherwise negative
  gamma, refined by net vanna when available: *vanna aligned* (net vanna
  < 0) or *vanna opposed* (net vanna ≥ 0).
- **GEX / vanna alignment.** One of four states from the two signs (zero
  counts as positive).
- **Index agreement.** Across the configured symbols: *aligned* when all
  share a regime, *partial* when a strict majority does, *divergent*
  otherwise, *unavailable* with fewer than two symbols or any regime missing.
  Its scope follows the main view: with *All expirations* chosen, every
  symbol's whole chain (the main view itself for the current symbol);
  otherwise each symbol's front expiration, the current symbol included,
  whichever expiration is chosen. The badge's help names the scope and the
  dates. [`test_index_agreement_follows_the_expiration_scope`]

Each regime's description states the hedging it implies under the assumed
positioning, in one or two sentences: for gamma, whether delta hedging buys
or sells as spot rises; for the vanna refinements, also which way a rise in
IV moves dealer delta and the hedge that offsets it. When net vanna is not
available a negative regime is not refined, and the badge's help says so;
the description itself never mentions missing data.
[`test_hedging_statements_name_the_assumption`]
- **Dealer delta sensitivities.** Net GEX (per 1% move), net vanna (per vol
  point) and net charm (per trading day), all in dollars of delta, with the
  charm still to come in today's session (one day's charm × the share of the
  session left). The largest in absolute value is named, with the caveat
  that other shock sizes rank them differently.

## Unusual volume

**Definition.** A contract is flagged when its volume-to-open-interest ratio,
volume / (OI + 1), is at least the threshold (3× by default) times the
median ratio of the eleven listed strikes centred on it (itself and up to
five either side), in the same expiration and right, counting only
contracts that traded, and it traded at least 10 contracts. At least three
traded contracts are needed in the window. Flags are listed most unusual
first. (`flow.volume_spikes`)

**Reason.** Raw volume differs by orders of magnitude between the money and
the wings and between a 0DTE expiration and a monthly, so a chain-wide mean
flags whole regions rather than single contracts, and it moves when the
strike range adds or removes quiet wings. Dividing by OI removes the size
differences between strikes (round strikes, the OI peaks), the local median
is robust to the spikes themselves, and the window depends only on
neighbouring strikes, so widening the strike range leaves every contract at
least five strikes inside it unchanged. The 10-contract floor keeps a few
lots on a quiet far strike from swinging the ratio. On the synthetic market
the rule flags the seeded spikes and nothing else.
[`test_threshold_and_order`, `test_baseline_is_relative_to_open_interest`,
`test_widening_the_strike_range_keeps_the_near_the_money_flags`,
`test_fewer_than_min_neighbours_traded_gives_no_flag`,
`test_flags_the_seeded_spikes`]

## GEX ladder

`patterns.py` labels the strikes within a window of spot (±3% by default;
the tab's slider runs from 1% to 10%) from the GEX-by-strike profile alone.
The peak node's absolute GEX sets the scale:

| Rule | Definition | Default |
|---|---|---|
| Regime | *positive* if GEX > `neutral_pct` × peak, *negative* if < −that, else *neutral* | 5% |
| Peak node | the strike with the largest absolute GEX in the window | |
| Low-exposure gap | absolute GEX under `low_exposure_pct` × peak | 10% |
| Floor / ceiling | the positive strike with the most GEX below / above spot | |
| Intermediate node | a non-neutral strike between spot and the floor or ceiling that is neither the peak node nor a gap | |
| Upside gamma stack | a negative strike above spot, and more positive than negative strikes below | |
| Downside gamma stack | more positive than negative strikes above spot, and more negative than positive below | |
| Positive-gamma cluster | at least `min_nodes` neighbouring strikes (consecutive in the chain's strike list), each positive and ≥ `magnitude_threshold` × peak | 3 strikes, 50% |
| Deep-ITM saturation | a negative-regime strike whose OI-weighted \|delta\| over every contract there is ≥ the threshold, with at least `min_oi` contracts | 0.85, 100 |

**Arrangement** (`bias`), checked in order: *pinned* when the peak node is
within 0.15% of spot; *upside-weighted* for an upside gamma stack and
*downside-weighted* for a downside stack, wherever the peak node is; then,
without a stack, *upside-weighted* when at least two positive strikes are
below spot and positive outnumber negative above; *downside-weighted* when
negative outnumber positive on both sides; otherwise *neutral*. A stack
decides the arrangement whenever one is present, so the badge never names
one side while the stack sentence names the other.
[`test_badge_agrees_with_the_stack_sentence`,
`test_arrangement_never_contradicts_the_stack`]

The tab states each finding in a sentence: the peak node, the floor and
ceiling, intermediate nodes, the counts on each side, any gamma stack, and
the low-exposure gaps on either side whenever there is at least one.
[`test_a_single_gap_on_one_side_is_reported`]

**Decision.** The thresholds are named parameters with stated defaults, and
the tab's explainer is generated from the same rules (a page test checks that
it quotes the core defaults). The ladder reports structure only: it attaches
no probabilities or reaction rates to any label, because nothing in the data
estimates them.

## Simulation

`simulation.simulate_paths` draws Monte Carlo price paths in one-minute
steps:

```text
σ_eff(S)  = σ_base × (1 − d × tanh(GEX(S) / max|GEX|))
S_{t+1}   = S_t × (1 + σ_eff(S_t) × √Δt × z),   z ~ N(0, 1),   Δt = 1 / (252 × 390)
```

GEX(S) is the per-strike GEX profile interpolated linearly at the path's
price and taken as 0 outside the listed strikes (it is not repriced, unlike
the gamma flip). Volatility is lower where that GEX is positive and higher
where it is negative, by at most the factor d. Drift is zero.

- σ_base is the front expiration's ATM IV, or a value entered in the tab.
- d (dampening, default 0.4) is a free parameter, not an estimate.
- Normalizing by the profile's largest |GEX| makes the model independent of
  GEX units.
- The profile is held fixed for the whole run.
- The run covers the rest of today's session, or the whole next session
  outside it. 500 paths by default; the seed is fixed (0), so the same inputs
  give the same chart.

The tab shows the 5th, 25th, 50th, 75th and 95th percentiles at every
minute. They describe this model's dispersion, not a forecast.

## Synthetic market

**Decision.** The default provider is a seeded market (`providers/synthetic.py`),
priced with the same `bsm` functions the core uses:

- **Spot.** Each session opens within about ±1% of the symbol's base price
  and follows a seeded one-minute random walk with volatility equal to the
  symbol's ATM IV. Before the open it is the open; after the close and at
  weekends it is the session's close.
- **Expirations.** Today (until 16:00), the next trading day, the next four
  unexpired Fridays (a Friday counts itself only until its close, so the
  list is the same on Friday evening as on Saturday) and the next three
  third-Friday monthlies.
- **Volatility.** A smile in log-moneyness m = ln(K/S):
  iv = atm + skew × m + curvature × m², floored at 5%.
- **Open interest.** Gaussian bumps in log-moneyness from the open: calls
  peak about 2.5% above, puts about 3% below, scaled by a per-symbol
  put/call ratio, with extra interest at round strikes and on monthlies, and
  lognormal jitter. Same-day (0DTE) interest is centred nearer the money
  (0.3 × those distances, half the width): a same-day option's gamma is
  concentrated within a fraction of a percent of spot, so 0DTE interest 2–3%
  away would barely show next to the monthlies.
  [`test_0dte_open_interest_sits_near_the_money`]
- **Volume.** A full-day amount scaled by the share of the session elapsed;
  0DTE turns over most and its customer flow leans to buying (about 62% bought);
  three contracts per expiration get a 6× spike, which the unusual-volume
  rule finds. [`test_flags_the_seeded_spikes`]
- **Greeks** from BSM at trading-time T (at least one minute), converted to
  chain units.

**Determinism.** Every random draw comes from a generator seeded with
`[GEX_SYNTHETIC_SEED, crc32(symbol), session date, ...]`, so a chain depends
only on the seed, the symbol and the clock time: the same instant gives the
same chain, and one symbol's data does not depend on which others are asked
for. [`test_same_instant_same_chain`, `test_seed_and_symbol_change_the_market`]

**Reason.** The demo needs every feature to have something to show at any
hour without a data feed: a gamma flip between the walls, a vanna flip, 0DTE
contracts, flow and volume spikes. The shapes are chosen for that
[`test_flip_lies_between_the_walls`, `test_vanna_flip_exists`] and are
illustrative only.

## Other decisions

**Reference-line colors.** Each level has its own hue (spot gray, gamma flip
orange, vanna flip green, vega peaks cyan, expected move yellow), and the
vega peaks are dotted, since they often sit next to the gamma flip. Every
line is also named with its value, so none is identified by color alone. The
ladder table's "Share of peak" bar is gray: it is an unsigned share, and the
sign is in the Regime column.

**Capabilities, not provider names.** A provider declares `MULTI_EXPIRATION`,
`SECOND_ORDER` and `FLOW`; the UI checks flags, never names. A tab that
needs a missing capability says what the data source does not supply. Adding
a provider then needs no UI change.

**Lazy optional dependency.** `convexlib` is not on PyPI, so it is not a
dependency. It is imported only when the ConvexValue provider is
constructed, and a missing package becomes a `ProviderError` with the install
command.

**No storage.** Every tab works from the live chain, so there is no database,
schema or snapshot store. This keeps the demo to one container, at the cost
of intraday history (see the README's Limitations).

**One snapshot per view.** `build_market_view` reads the clock once and
derives everything from one spot and one chain. A value cached apart from
the chain could describe a different chain or symbol than the one on
screen, so nothing is.

**The strike range bounds every figure.** The provider returns only strikes
within the sidebar's range, and every figure (net GEX, the flip and its
curve, walls, profiles, heatmaps, ladder, flow, volume spikes and the
agreement badge) is computed from them. The Assumptions panel and the help
on Net GEX and the gamma flip say so.
