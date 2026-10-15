FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version /app/
COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/radar /app/backend/radar
RUN uv sync --locked --no-dev --group analytics --no-editable \
    && useradd --create-home radar
COPY analytics /app/analytics
COPY config /app/config
RUN chown -R radar:radar /app
USER radar
EXPOSE 8000
CMD ["uvicorn", "radar.api:app", "--host", "0.0.0.0", "--port", "8000"]
