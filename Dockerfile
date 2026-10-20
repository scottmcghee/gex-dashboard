# syntax=docker/dockerfile:1
# Base images are pinned by digest for reproducible builds; Dependabot bumps them.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
RUN useradd --create-home --uid 1000 dashboard
# Only the virtualenv is copied, owned by root: the runtime user can't modify the code.
COPY --from=build /app/.venv /app/.venv
# Headless, no usage statistics, the viewer toolbar (no developer "Deploy"
# menu), and a fixed browser address: without one, a headless Streamlit looks up
# the machine's public IP to print an "External URL". The data source is the
# synthetic market on a replay clock unless GEX_PROVIDER says otherwise.
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_CLIENT_TOOLBAR_MODE=viewer STREAMLIT_BROWSER_SERVER_ADDRESS=localhost \
    GEX_PROVIDER=synthetic
USER dashboard
WORKDIR /home/dashboard
EXPOSE 8501
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=20 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"]
CMD ["gex-dashboard", "--server.address=0.0.0.0", "--server.port=8501"]
