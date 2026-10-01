# Model Buk always-on jobs + password-protected scanner page, one Railway service with a volume at /data.
# Build context = the repository root (or the staged context from deploy/railway/stage_context.py, same layout):
#     docker build -f deploy/railway/Dockerfile .
# Runs as root: Railway mounts volumes as root (a non-root image would need RAILWAY_RUN_UID=0 anyway).
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Warsaw \
    MODEL_BUK_DATA=/data \
    APP_DIR=/opt/model-buk/app \
    PYTHONPATH=/opt/model-buk/app/src \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    PORT=8080

# tini: PID 1 (reaps zombies, forwards SIGTERM); tzdata: Europe/Warsaw; util-linux: flock(1)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini tzdata ca-certificates util-linux \
 && rm -rf /var/lib/apt/lists/*

# supercronic (cron for containers), pinned release, verified by SHA-256 (GitHub release digest) and SHA-1
# (the checksum published in the release notes) before it is installed.
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA1=e63c11a9726b775a6a11801e81af4f3fb926aa68
RUN python -c "import hashlib, os, urllib.request; \
url = 'https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64'; \
data = urllib.request.urlopen(url, timeout=120).read(); \
assert hashlib.sha256(data).hexdigest() == '${SUPERCRONIC_SHA256}', 'supercronic sha256 mismatch'; \
assert hashlib.sha1(data).hexdigest() == '${SUPERCRONIC_SHA1}', 'supercronic sha1 mismatch'; \
open('/usr/local/bin/supercronic', 'wb').write(data); os.chmod('/usr/local/bin/supercronic', 0o755)" \
 && supercronic -version

COPY app/requirements-server.txt /opt/model-buk/app/requirements-server.txt
RUN pip install -r /opt/model-buk/app/requirements-server.txt \
 && pip install --no-deps catboost==1.2.10

COPY app/src /opt/model-buk/app/src
COPY app/scripts /opt/model-buk/app/scripts
COPY app/config /opt/model-buk/app/config
COPY deploy/railway /opt/model-buk/deploy/railway

# Windows checkouts may carry CRLF: normalise the shell scripts and the crontab, then check that the crontab
# parses and that every job imports (no scikit-learn, no matplotlib needed).
RUN find /opt/model-buk/deploy/railway -name '*.sh' -exec sed -i 's/\r$//' {} + \
 && sed -i 's/\r$//' /opt/model-buk/deploy/railway/crontab \
 && chmod +x /opt/model-buk/deploy/railway/jobs/*.sh \
 && supercronic -test /opt/model-buk/deploy/railway/crontab \
 && python -c "import sys; sys.path.insert(0, '/opt/model-buk/app/scripts'); \
import download_history, model_buk.scanner.pipeline, model_buk.scanner.live_models, model_buk.maintenance.bucket_sync, \
model_buk.maintenance.odds_retention, model_buk.maintenance.register_retention, model_buk.ingestion.api_football_tables, \
model_buk.ingestion.closing_odds, catboost, boto3; from catboost import CatBoostClassifier; print('imports ok')"

WORKDIR /opt/model-buk/app
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/opt/model-buk/deploy/railway/entrypoint.py"]
