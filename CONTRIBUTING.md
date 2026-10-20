# Contributing

## Setup

```bash
uv sync                     # Python 3.12+; installs the package and dev tools into .venv
uv run pre-commit install   # lint, format, type-check and secret-scan on each commit
```

Nothing else is needed: there is no database, and the tests and the default
app use the synthetic market. Docker is only for the demo image.

## Checks

```bash
uv run ruff check .                      # lint
uv run ruff format .                     # format (CI runs --check)
uv run mypy                              # strict type check of src/
uv run pytest                            # every test, with the coverage floor
uv run pytest tests/unit/test_levels.py --no-cov   # a subset, without the floor
uv run pre-commit run --all-files        # every pre-commit hook
uv run gex-dashboard                     # the app on http://localhost:8501
docker compose up --build                # the demo image
```

`pyproject.toml` adds `--cov` to every pytest run, so a plain `uv run pytest`
enforces the floor. A subset can't reach it; pass `--no-cov` when running
one. CI runs the same checks on every push to `main` and on every pull
request, plus the Docker demo and a gitleaks scan. ruff also formats the
Python code blocks in Markdown files, so a snippet that isn't valid,
formatted Python belongs in a `text` or `bash` fence.

### Tests

| | Unit (`tests/unit`) | Page (`tests/pages`) |
|---|---|---|
| Runs | the core, the providers, the charts | the whole app under Streamlit's `AppTest` |
| Data | small hand-built chains (`make_chain`, `gex_profile` in `conftest.py`) and the synthetic provider on a `FixedClock` | the synthetic provider on a fixed clock (Monday 2026-10-19, 11:00 New York time), plus test providers registered in the registry |
| Network | none: ConvexValue runs against `tests/unit/fake_convexlib.py` | none |

The page tests clear the settings and Streamlit caches around every test and
set `GEX_CLOCK=fixed`, so they don't depend on the time of day. Any test can
override the environment with `monkeypatch.setenv`.

### Coverage

The floor is 90% of lines and branches (`[tool.coverage]` in
`pyproject.toml`). `app.py` and `__main__.py` are left out of the
measurement: coverage can't trace a script running inside `AppTest`, and the
entry point only hands over to Streamlit. The page tests still run `app.py`,
and the tab modules it imports are measured.

## Conventions

- **Units are converted only in `exposures.py`.** Providers return per-share
  greeks in the chain units of `providers/base.py`; nothing else multiplies
  by the contract size or spot. A new exposure gets a `dollar_*` function and
  a unit test that checks it against a direct revaluation.
- **Time comes from the clock.** Core code takes `now` or a `Clock`; it never
  calls `datetime.now()`. Time to expiry is `clock.years_to_expiry`.
- **Missing is NaN, not 0.** A greek a source doesn't supply stays NaN.
- **Capabilities, not names.** UI code checks `Capability` flags, never a
  provider's name; a tab that needs a missing capability calls
  `ui.context.require`.
- **Describe, don't advise.** Labels and captions say what was computed and
  under which assumption. No forecasts, probabilities or performance claims
  in UI text, docstrings or comments.
- **The core has no Streamlit.** Calculations live in the core modules and
  are unit-tested; tabs read a `MarketView` and render it.
- Logging goes through `logging`, never `print`, in library code. Catch
  specific exceptions; a broad `except` belongs only around a vendor client
  whose exceptions are undocumented, and it must re-raise as `ProviderError`.

## Adding a provider

1. **Implement the protocol** (`providers/base.py`): a class with `name`,
   `capabilities` and the methods `spot`, `expirations` and `chain`.
   `chain` returns exactly `chain_columns(capabilities)`, one row per
   contract, in the documented units, with strikes within `strike_range` of
   spot and only the expirations asked for (all of them when `None`, if it
   declares `MULTI_EXPIRATION`). Use `empty_chain(capabilities)` for no rows.
   Accept `now` and use it at least for calendar decisions. Raise
   `ProviderError` for every failure; import any optional client lazily, so
   the package works without it.
2. **Register it** in `PROVIDERS` in `providers/__init__.py` with a factory
   `(Settings, Clock) -> ChainProvider`. Credentials and options come from
   new `Settings` fields read from the environment (no secret defaults; keep
   secrets out of `repr`), documented in `config.py`'s docstring,
   `.env.example` and the README.
3. **Run the contract suite.** `tests/unit/test_provider_contract.py`
   parametrizes over the registry: add a branch to its `provider` fixture
   that builds yours without network access (a fake client, like
   `fake_convexlib.py`, fed from the synthetic chain), and add the name to
   `test_registry_covers_every_provider`. Every contract test must pass.
4. **Test the mapping** in its own module: unit conversions, missing values,
   malformed payloads and errors, as `test_convexvalue.py` does.
5. **Label it** in `PROVIDER_LABELS` in `app.py`, and add a page test that
   renders the app with it, like `test_convexvalue_renders_with_the_fake_api`.

## Adding a tab

Add `tabs/<name>.py` with `render(ctx: TabContext) -> None`, put any figure
in `charts/`, and add the title and function to `TABS` in `app.py`. Gate
optional features with `require` or `require_several_expirations`. Add the
title to `TAB_TITLES` in `tests/pages/test_pages.py` (the CI docker job also
counts the tabs) and a test that drives its controls.
