from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI, HTTPException

from common.models import ProcessingRequest, WorkRequest, WorkResponse, utc_now
from common.runtime import configure_logging, identity, install_standard_endpoints


SERVICE = "ingestion"
PROCESSING_URL = os.getenv("PROCESSING_URL", "http://processing:8000").rstrip("/")
DOWNSTREAM_TIMEOUT_SECONDS = float(os.getenv("DOWNSTREAM_TIMEOUT_SECONDS", "30"))

configure_logging()
logger = logging.getLogger(SERVICE)
app = FastAPI(title="NET 470 Ingestion Service", version="3.0.0")
install_standard_endpoints(app, SERVICE)


async def forward_to_processing(payload: ProcessingRequest) -> WorkResponse:
    async with httpx.AsyncClient(timeout=DOWNSTREAM_TIMEOUT_SECONDS, trust_env=False) as client:
        response = await client.post(
            f"{PROCESSING_URL}/v1/process",
            json=payload.model_dump(mode="json"),
        )
        response.raise_for_status()
        return WorkResponse.model_validate(response.json())


@app.post("/v1/jobs", response_model=WorkResponse)
async def submit_job(request: WorkRequest) -> WorkResponse:
    received_at = utc_now()
    ingestion_identity = identity(SERVICE)
    forwarded = ProcessingRequest(
        **request.model_dump(),
        ingestion_received_at_utc=received_at,
        ingestion_identity=ingestion_identity,
    )
    logger.info(
        "request accepted",
        extra={
            "service": SERVICE,
            "event": "ingestion_received",
            "run_id": request.run_id,
            "request_id": request.request_id,
                "pod_name": ingestion_identity.pod_name,
                "pod_uid": ingestion_identity.pod_uid,
                "node_name": ingestion_identity.node_name,
                "namespace": ingestion_identity.namespace,
                "image_version": ingestion_identity.image_version,
        },
    )
    try:
        result = await forward_to_processing(forwarded)
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception(
            "processing service request failed",
            extra={
                "service": SERVICE,
                "event": "processing_call_failed",
                "run_id": request.run_id,
                "request_id": request.request_id,
                "pod_name": ingestion_identity.pod_name,
                "pod_uid": ingestion_identity.pod_uid,
                "node_name": ingestion_identity.node_name,
                "namespace": ingestion_identity.namespace,
                "image_version": ingestion_identity.image_version,
            },
        )
        raise HTTPException(status_code=502, detail="processing service unavailable") from exc

    result.timestamps.ingestion_response_at_utc = utc_now()
    logger.info(
        "request completed",
        extra={
            "service": SERVICE,
            "event": "ingestion_response",
            "run_id": request.run_id,
            "request_id": request.request_id,
                "pod_name": ingestion_identity.pod_name,
                "pod_uid": ingestion_identity.pod_uid,
                "node_name": ingestion_identity.node_name,
                "namespace": ingestion_identity.namespace,
                "image_version": ingestion_identity.image_version,
        },
    )
    return result
