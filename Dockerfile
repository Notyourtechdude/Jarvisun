FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 jarvis \
    && mkdir -p /app /data && chown jarvis:jarvis /app /data
WORKDIR /app
COPY requirements-lock.txt /app/requirements-lock.txt
RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt
COPY src /app/src
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER jarvis
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=2)"
CMD ["python", "-m", "jarvis.cli", "serve", "--config", "/data/config.toml", "--host", "0.0.0.0"]
