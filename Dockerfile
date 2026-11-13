# DataWatch — production image for the shared OVH VPS (SLATE box).
# Built ON the VPS at deploy time from the repo checkout (see
# deploy/deploy-remote.sh) — deliberately not in GitHub Actions.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY static static
COPY templates templates
# Bundled sample CSVs only — never the dev database (data/datawatch.db).
COPY data/sample_orders_clean.csv data/sample_orders_clean.csv
COPY data/sample_orders_dirty.csv data/sample_orders_dirty.csv
RUN useradd --create-home app \
    && mkdir -p /data \
    && chown -R app:app /data

# The SQLite database lives on the persistent volume, not in the image.
ENV DATAWATCH_DB=/data/datawatch.db
USER app
VOLUME /data
EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/healthz', timeout=4)"

# One worker keeps a single scheduler thread (monitor.start_scheduler);
# threads serve requests.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
