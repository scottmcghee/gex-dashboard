# Extras

`gex_levels_indicator.pine` is a TradingView Pine Script (v6) indicator that
draws the dashboard's levels as horizontal lines on a chart: the gamma flip,
up to three call and put walls, the expected-move range, the vega peaks and
the vanna flip. It does not fetch data. Copy the levels string from the Live
Levels tab (for example `GF=584.16|CW1=590|PW1=570|EU=587.38|EL=577.24`),
paste it into the indicator's **Levels** input, and paste a new string
whenever the levels change.
