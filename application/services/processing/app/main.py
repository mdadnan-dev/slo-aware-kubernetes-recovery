from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI, HTTPException

from common.models import (
    IdentityChain,
    ProcessingRequest,
    ResultAck,
    ResultSubmission,
    StageTimestamps,
    WorkResponse,
    utc_now,
)
from common.runtime import configure_logging, identity, install_standard_endpoints


SERVICE = "processing"
RESULTS_URL = os.getenv("RESULTS_URL", "http://results:8000").rstrip("/")
DOWNSTREAM_TIMEOUT_SECONDS = float(os.getenv("DOWNSTREAM_TIMEOUT_SECONDS", "30"))

configure_logging()
logger = logging.getLogger(SERVICE)
app = FastAPI(title="NET 470 Processing Service", version="3.0.0")
install_standard_endpoints(app, SERVICE)


def deterministic_cpu_work(work_units: int) -> str:
    value = 0x9E3779B9
    for index in range(work_units):
        value = ((value ^ index) * 1664525 + 1013904223) & 0xFFFFFFFF
    return f"{value:08x}"


def store_result(submission: ResultSubmission) -> ResultAck:
    with httpx.Client(timeout=DOWNSTREAM_TIMEOUT_SECONDS, trust_env=False) as client:
        response = client.post(
            f"{RESULTS_URL}/v1/results",
            json=submission.model_dump(mode="json"),
        )
        response.raise_for_status()
        return ResultAck.model_validate(response.json())


@app.post("/v1/process", response_model=WorkResponse)
def process_job(request: ProcessingRequest) -> WorkResponse:
    received_at = utc_now()
    processing_identity = identity(SERVICE)
    started_at = utc_now()
    checksum = deterministic_cpu_work(request.work_units)
    completed_at = utc_now()
    submission = ResultSubmission(
        run_id=request.run_id,
        request_id=request.request_id,
        work_units=request.work_units,
        payload=request.payload,
        checksum=checksum,
        submitted_at_utc=request.submitted_at_utc,
        ingestion_received_at_utc=request.ingestion_received_at_utc,
        processing_received_at_utc=received_at,
        processing_started_at_utc=started_at,
        processing_completed_at_utc=completed_at,
        ingestion_identity=request.ingestion_identity,
        processing_identity=processing_identity,
    )
    try:
        ack = store_result(submission)
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception(
            "results service request failed",
            extra={
                "service": SERVICE,
                "event": "results_call_failed",
                "run_id": request.run_id,
                "request_id": request.request_id,
                "pod_name": processing_identity.pod_name,
                "pod_uid": processing_identity.pod_uid,
                "node_name": processing_identity.node_name,
                "namespace": processing_identity.namespace,
                "image_version": processing_identity.image_version,
            },
        )
        raise HTTPException(status_code=502, detail="results service unavailable") from exc

    logger.info(
        "processing completed",
        extra={
            "service": SERVICE,
            "event": "processing_completed",
            "run_id": request.run_id,
            "request_id": request.request_id,
                "pod_name": processing_identity.pod_name,
                "pod_uid": processing_identity.pod_uid,
                "node_name": processing_identity.node_name,
                "namespace": processing_identity.namespace,
                "image_version": processing_identity.image_version,
        },
    )
    return WorkResponse(
        run_id=request.run_id,
        request_id=request.request_id,
        work_units=request.work_units,
        checksum=checksum,
        timestamps=StageTimestamps(
            submitted_at_utc=request.submitted_at_utc,
            ingestion_received_at_utc=request.ingestion_received_at_utc,
            processing_received_at_utc=received_at,
            processing_started_at_utc=started_at,
            processing_completed_at_utc=completed_at,
            result_received_at_utc=ack.result_received_at_utc,
            result_stored_at_utc=ack.result_stored_at_utc,
        ),
        identities=IdentityChain(
            ingestion=request.ingestion_identity,
            processing=processing_identity,
            results=ack.results_identity,
        ),
    )
