ARG PYTHON_BASE_IMAGE=python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    IMAGE_VERSION=q1

WORKDIR /app
COPY requirements/runtime-lock.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser
COPY common /app/common
COPY services /app/services
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "services.ingestion.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
