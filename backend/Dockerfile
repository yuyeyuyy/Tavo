FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.11 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker-entrypoint.sh /usr/local/bin/tavo-entrypoint
RUN groupadd --gid 10001 tavo && useradd --uid 10001 --gid tavo --no-create-home tavo \
    && mkdir -p /data /keys && chown tavo:tavo /data /keys \
    && chmod 700 /data /keys && chmod 755 /usr/local/bin/tavo-entrypoint
ENV PATH="/app/.venv/bin:$PATH" TAVO_DATA_DIR=/data TAVO_KEYRING_FILE=/keys/master.keys TAVO_CLIENT_TOKEN_FILE=/keys/client.token
USER tavo
EXPOSE 8000
ENTRYPOINT ["tavo-entrypoint"]
CMD ["api"]
