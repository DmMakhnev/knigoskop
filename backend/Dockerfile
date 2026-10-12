# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN --mount=type=secret,id=trust_bundle \
    if [ -f /run/secrets/trust_bundle ]; then export PIP_CERT=/run/secrets/trust_bundle; fi; \
    pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
RUN useradd --create-home appuser
RUN mkdir -p /app/.local/files /app/.local/mail && chown -R appuser:appuser /app/.local
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
