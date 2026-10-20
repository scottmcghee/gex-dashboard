# Requirements

Each requirement names the tests that verify it, or says how it is met when
it isn't directly testable. Page tests are in `tests/pages/test_pages.py`
and run the whole app under Streamlit's `AppTest`; the other tests are in
`tests/unit`.

## Functional

| ID | Requirement | Verified by |
|---|---|---|
| FR-1 | Show one symbol's dealer exposure for a chosen expiration, or all expirations when the provider supports it, within a chosen strike range that every figure is limited to (stated in the Assumptions panel and the Net GEX and gamma-flip help): metric cards (net GEX, gamma flip, top walls, gamma ratio, vanna flip, customer net delta, GEX/vanna state), a regime badge, an index-agreement badge whose scope (all expirations, or each symbol's front expiration) follows the main view, reusing it rather than loading it again, and is named in its help, and seven tabs. The GEX profile's strike axis is zoomed to the strikes that carry exposure, with spot and every level in view. Expirations whose close has passed are not offered or counted. | `test_every_tab_renders`, `test_single_expiration_profile_zooms_to_the_strikes_with_exposure`, `test_profile_zoom_keeps_a_distant_level_in_view`, `test_every_symbol_renders`, `test_badges_and_assumptions`, `test_index_agreement_follows_the_expiration_scope`, `test_all_expirations_reuses_the_main_view`, `test_front_scope_reuses_the_main_view_when_it_is_the_front`, `test_agreement_badge_reuses_the_main_view`, `test_expirations_closed_at_now_are_dropped` |
| FR-2 | Convert every contract's greeks to dollar exposures in one place, with the documented units: GEX per 1% move, delta, vanna per vol point, charm per trading day, gross vega. | `test_gamma_is_dollars_per_one_percent_move`, `test_gamma_matches_a_one_percent_move`, `test_vanna_matches_a_vol_point`, `test_charm_matches_one_trading_day`, `test_vega_is_dollars_per_vol_point`, `test_contract_exposures_applies_the_conventions` |
| FR-3 | Apply one dealer sign convention (long calls, short puts) to delta, gamma, vanna and charm, and state it in the UI. | `test_single_constant`, `test_puts_are_negative_calls_positive`, `test_vanna_and_charm_follow_the_sign_vega_does_not`, `test_badges_and_assumptions` |
| FR-4 | Find the gamma flip as the price where net GEX, repriced with Black-Scholes-Merton across spot ±10%, crosses from negative to positive, on the side of spot set by the repriced curve's sign at spot (a disagreeing net GEX from the chain's own gamma is logged); when there is none, say whether nothing could be repriced or no crossing lies on that side. | `test_at_spot_equals_the_chain_gex`, `test_reprices_at_other_prices`, `test_interpolates_the_crossing_below_spot_when_positive`, `test_crossing_above_spot_when_negative`, `test_a_disagreeing_net_gex_is_logged_and_the_curve_decides`, `test_a_disagreeing_vendor_gamma_leaves_the_side_to_the_curve`, `test_gamma_flip_is_a_rising_zero_of_the_repriced_curve`, `test_matches_the_closed_form`, `test_is_a_price_not_a_strike`, `test_one_sign_returns_none`, `TestGammaFlipNote`, `test_zero_open_interest_shows_n_a`, `test_levels_tab_describes_the_flip_as_a_price` |
| FR-5 | Report up to three call and put walls, the vanna flip, vega peaks, the gamma ratio and the front (nearest unexpired) expiration's 1σ expected move (S × ATM IV × √T in trading time), labelled with that expiration on multi-expiration heatmaps, and export them as a TradingView levels string. | `test_top_call_and_put_walls`, `test_zero_gex_strikes_are_never_walls`, `test_finds_sign_change`, `test_zero_strike_at_the_crossing_does_not_hide_it`, `test_peaks_above_and_below`, `test_one_standard_deviation`, `test_atm_iv_is_call_put_average_at_nearest_strike`, `test_expected_move_at_uses_one_expiration_and_the_clock`, `test_all_levels_in_order`, `test_all_calls_chain_has_no_put_walls`, `test_heatmap_names_the_expected_moves_expiration` |
| FR-6 | Label the net exposures (gamma regime, GEX/vanna alignment, agreement across symbols, dealer delta sensitivities) descriptively, with no trading advice; each regime states the hedging it implies under the assumed positioning, and missing vanna is shown by the UI. | `test_classification`, `test_hedging_statements_name_the_assumption`, `test_gated_tabs_explain_missing_capabilities`, `test_four_states`, `test_aligned`, `test_partial`, `test_divergent`, `test_unavailable`, `test_dominant_by_absolute_value`, `test_charm_remaining_scales_with_session`, `test_descriptions_give_no_advice` |
| FR-7 | GEX Ladder: classify the strikes near spot by the documented rules, summarize their arrangement in plain statements, with an arrangement badge that never contradicts the gamma stack it names, and find positive-gamma clusters and deep-ITM saturation. | `test_regimes_with_neutral_band`, `test_floor_ceiling_and_intermediate_nodes`, `test_upside_gamma_stack`, `test_downside_gamma_stack`, `test_stack_decides_the_arrangement_wherever_the_peak_is`, `test_badge_agrees_with_the_stack_sentence`, `test_arrangement_never_contradicts_the_stack`, `test_ladder_badge_agrees_with_the_stack_sentence`, `test_pinned_when_peak_at_spot`, `test_single_cluster`, `test_adjacency_follows_the_chain_grid`, `test_saturated_strike`, `test_interpretation_describes_without_advice`, `test_a_single_gap_on_one_side_is_reported`, `test_ladder_share_of_peak_bar_is_neutral`, `test_ladder_shows_structure_and_explainer`, `test_ladder_explainer_matches_the_core_defaults` |
| FR-8 | Customer flow: net bought-minus-sold contracts, premium, dollar delta and dollar gamma per expiration and per strike × expiration, from the customer side. Unusual volume: contracts whose volume / OI is a multiple of the median of nearby strikes in the same expiration and right, independent of the strike range for contracts well inside it. | `test_columns_and_units`, `test_bought_puts_are_negative_delta`, `test_split_by_right`, `test_matrix`, `test_summary`, `test_summary_matches_the_per_metric_totals`, `test_threshold_and_order`, `test_baseline_is_relative_to_open_interest`, `test_baseline_is_per_expiration_and_right`, `test_widening_the_strike_range_keeps_the_near_the_money_flags`, `test_fewer_than_min_neighbours_traded_gives_no_flag`, `test_flags_the_seeded_spikes`, `test_includes_flow_split`, `test_tab_controls` |
| FR-9 | Exposure Heatmap: gamma, vanna or charm exposure by strike and expiration for the nearest ten expirations, with spot and ±1σ labels readable over the cells. | `test_strike_by_expiration`, `test_heatmap_move_labels_sit_on_a_readable_box`, `test_tab_controls`, `test_single_expiration_explains_the_cross_expiration_tabs` |
| FR-10 | Simulation: seeded Monte Carlo paths to the close with GEX-dependent volatility, percentile bands and a summary; outside the session, the next full session. | `test_positive_gex_narrows_negative_widens`, `test_zero_dampening_ignores_gex`, `test_seed_reproducibility`, `test_bands_are_ordered`, `test_simulation_window`, `test_simulation_starts_at_the_next_open_outside_the_session`, `test_simulation_shows_no_share_of_paths` |
| FR-11 | Read every setting from the environment, with no secret defaults; choose the provider and clock by name; reject invalid values with an error that names the variable (a strike range outside the slider's 0.02–0.25, for one). | `test_defaults`, `test_symbols_parsing`, `test_invalid_values`, `test_strike_range_outside_the_slider_bounds_names_the_variable`, `test_live_provider_defaults_to_system_clock`, `test_default_is_synthetic`, `test_unknown_name`, `test_convexvalue_reads_credentials_from_env` |
| FR-12 | Every provider meets one contract: canonical columns and types for its capabilities, strike range and expiration filters, and a chain the core can analyze. | `tests/unit/test_provider_contract.py`, run against every registered provider |
| FR-13 | ConvexValue adapter: map vendor fields to canonical units, keep missing values missing, track the vendor's expiration list per market day, log in again once on an expired session. | `test_vendor_units_converted`, `test_zero_oi_leaves_second_order_unknown`, `test_missing_greek_is_not_a_zero_exposure`, `test_refetched_on_a_new_market_date`, `test_shifted_list_is_refetched_and_retried`, `test_no_rows_after_the_list_shrinks_refetches`, `test_relogin_once_on_auth_error`, `test_convexvalue_renders_with_the_fake_api` |
| FR-14 | A self-contained demo: `docker compose up` serves the app on the synthetic market with no database, feed or API key, with a live 0DTE expiration at any hour whose gamma is visible next to the monthlies. | CI `docker` job; `test_0dte_open_interest_sits_near_the_money`, `test_replay_clock_advances_and_wraps`, `test_replay_start_outside_a_session_rolls_forward`, `test_every_tab_renders` (before the open, in session, after the close, Saturday) |

## Nonfunctional

| ID | Requirement | How it is met / verified |
|---|---|---|
| NFR-1 **Correctness** | Black-Scholes greeks match finite differences and put-call parity; exposures match a direct revaluation for a 1% move, a vol point and a trading day. | `test_delta`, `test_gamma`, `test_vega`, `test_vanna`, `test_theta`, `test_charm` (finite differences), `test_put_call_parity`, `test_vanna_matches_a_finite_difference_of_delta`, `test_charm_matches_a_finite_difference_of_delta`, `test_gamma_matches_a_one_percent_move` |
| NFR-2 **One snapshot** | Spot, chain, levels and expected move in a view describe one instant: the clock is read once per view, and no value is cached apart from the chain. | `analysis.build_market_view`; `test_accepts_an_explicit_now`, `test_expected_move_at_uses_one_expiration_and_the_clock` |
| NFR-3 **Trading time** | Time to expiry counts only regular-session minutes (252 × 390 per year); options expire at the 16:00 close. Holidays are not modelled. | `test_years_to_expiry`, `test_cases`, `test_fraction_remaining`; [DESIGN.md](DESIGN.md#trading-time) |
| NFR-4 **Deterministic demo** | The synthetic market depends only on the seed, the symbol and the clock time; one symbol's data does not depend on the others; the simulation uses a fixed seed. | `test_same_instant_same_chain`, `test_seed_and_symbol_change_the_market`, `test_seed_reproducibility`; the benchmark below built the same view twice from fresh providers and got identical exposures |
| NFR-5 **Failure handling** | A provider that cannot start or return data raises `ProviderError`; the app shows one message naming the data source and stops, with no traceback. Missing capabilities, an empty expiration list and a chain without open interest each get an explanation. | `test_provider_that_cannot_start_shows_an_error`, `test_chain_failure_shows_an_error`, `test_unknown_provider_shows_an_error`, `test_empty_chain_shows_a_warning`, `test_zero_open_interest_shows_n_a`, `test_gated_tabs_explain_missing_capabilities`, `test_missing_package`, `test_login_failure`, `test_malformed_rows`, `test_still_wrong_after_refetch_raises` |
| NFR-6 **Missing data is not zero** | A greek the source does not supply stays NaN and is left out of sums; a metric is unavailable only when no contract has it. | `test_missing_values_stay_missing`, `test_partial_nan_gamma_is_left_out_of_sums`, `test_all_nan_is_unavailable`, `test_missing_second_order_columns_are_unavailable` |
| NFR-7 **No network in tests** | No test makes a network call: the ConvexValue adapter runs against an in-memory fake `convexlib`. | The full suite passes inside a network namespace with no interfaces (`unshare -rn pytest`: 538 passed) |
| NFR-8 **Performance** | A view (fetch, convert, reprice, derive) builds in well under a second for thousands of contracts; a full app run renders in about a second cold and a quarter of a second from the cache. | Measured: see [Performance](#performance) |
| NFR-9 **Bounded memory** | Nothing is stored between views except the 60 s Streamlit caches; memory stays in the low hundreds of MB. | Measured: see [Performance](#performance) |
| NFR-10 **Security** | No secrets in code or defaults; the password stays out of `repr`; credentials come only from the environment. The container runs as a non-root user that can't modify the installed code, and the demo port binds to 127.0.0.1. | `test_defaults`, `test_password_is_not_in_repr`, `test_convexvalue_without_credentials`, gitleaks in CI and pre-commit; Dockerfile, docker-compose.yml |
| NFR-11 **Honest claims** | Labels, captions and explainers describe what is computed, with no forecasts, probabilities or trading advice. | `test_descriptions_give_no_advice`, `test_interpretation_describes_without_advice`, `test_no_directional_words`, `test_hedging_statements_name_the_assumption` |
| NFR-12 **Portability** | Python 3.12 to 3.14; runs offline in Docker. | CI matrix (3.12, 3.13, 3.14); CI `docker` job |
| NFR-13 **Maintainability** | Strict typing of `src/`, lint and format checks, a 90% coverage floor (lines and branches), pinned CI actions and base images kept current by Dependabot. | CI `lint` and `test` jobs; `.github/dependabot.yml` |

## Performance

Measured on 2026-10-19 on a laptop (AMD Ryzen 5 7530U, 6 cores, 12 threads,
Python 3.12) with other work running, so the numbers are indicative. Times
are the median of five runs on the synthetic market at 11:00 New York time.

**Building a view** (`build_market_view`: spot, chain, exposures, profiles,
repriced GEX curve, levels, labels):

| Symbol and scope | Contracts | Chain | GEX curve | Whole view |
|---|---|---|---|---|
| SPY, front expiration, ±10% | 234 | 3 ms | 6 ms | 23 ms |
| SPY, all 9 expirations, ±10% | 2,106 | 13 ms | 24 ms | 53 ms |
| SPY, all expirations, ±25% | 5,238 | 13 ms | 35 ms | 69 ms |
| SPX, all expirations, ±10% | 4,176 | 15 ms | 39 ms | 74 ms |
| SPX, all expirations, ±25% | 10,404 | 21 ms | 59 ms | 104 ms |
| QQQ, all expirations, ±10% | 1,782 | 11 ms | 19 ms | 49 ms |

The GEX curve grows with the number of distinct (expiry, strike, IV)
groups × 201 prices; the rest is linear in contracts. A live provider adds
its own network time to the chain column.

**The app** (`AppTest`, default settings: SPY, all expirations, ±10%, three
symbols for the agreement badge):

| Run | Time |
|---|---|
| First run (empty caches; builds three views and renders every tab) | 1.0 s |
| Rerun within the cache lifetime | 0.20 s |
| Switch symbol to SPX (view already cached by the agreement badge) | 0.21 s |
| Switch to SPX's front expiration (new view, plus SPY's and QQQ's front expirations for the agreement badge) | 0.23 s |
| Simulation, 500 / 1,000 paths × 390 minutes | 42 ms / 52 ms |

**Memory.** Peak resident memory was 206 MB for a process that built every
view above, and 217 MB for an `AppTest` process running the app four times.
The idle demo container used 79 MB after startup. The full test suite (538
tests) runs in about 35 s without coverage (about 55 s with it) with a peak of
257 MB.

**Docker.** The first `docker compose build` on this machine, including
downloading the packages: about 2 minutes. `docker compose up --wait` until
healthy: 9 s. The image is 766 MB.
