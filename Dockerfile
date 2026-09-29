FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ELABFTW_MCP_CONFIG=/app/config.yml

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 mcp

COPY config.example.yml /app/config.yml
RUN mkdir -p /app/logs && chown -R mcp:mcp /app
USER mcp

EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8081/status', timeout=4)"

# Hosted (multi-user) mode by default: /register issues personal MCP URLs.
# For a single-user stdio deployment use: docker run -i ... elabftw-mcp --transport stdio
CMD ["elabftw-mcp", "--hosted", "--host", "0.0.0.0", "--port", "8081"]
