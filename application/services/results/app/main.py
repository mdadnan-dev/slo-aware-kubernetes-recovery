from __future__ import annotations

import logging
import os
from collections import deque
from threading import Lock
from uuid import UUID

from fastapi import FastAPI, HTTPException

from common.models import ResultAck, ResultSubmission, StoredResult, utc_now
from common.runtime import configure_logging, identity, install_standard_endpoints


SERVICE = "results"
MAX_RECORDS = int(os.getenv("RESULTS_MAX_RECORDS", "10000"))
if MAX_RECORDS < 1:
    raise RuntimeError("RESULTS_MAX_RECORDS must be at least 1")

configure_logging()
logger = logging.getLogger(SERVICE)
app = FastAPI(title="NET 470 Results Service", version="3.0.0")
install_standard_endpoints(app, SERVICE)

_records: deque[StoredResult] = deque(maxlen=MAX_RECORDS)
_index: dict[UUID, StoredResult] = {}
_lock = Lock()


@app.post("/v1/results", response_model=ResultAck)
def save_result(submission: ResultSubmission) -> ResultAck:
    received_at = utc_now()
    results_identity = identity(SERVICE)
    with _lock:
        if len(_records) == _records.maxlen and _records:
            oldest = _records[0]
            _index.pop(oldest.submission.request_id, None)
        stored_at = utc_now()
        record = StoredResult(
            submission=submission,
            result_received_at_utc=received_at,
            result_stored_at_utc=stored_at,
            results_identity=results_identity,
        )
        _records.append(record)
        _index[submission.request_id] = record
    logger.info(
        "result stored",
        extra={
            "service": SERVICE,
            "event": "result_stored",
            "run_id": submission.run_id,
            "request_id": submission.request_id,
                "pod_name": results_identity.pod_name,
                "pod_uid": results_identity.pod_uid,
                "node_name": results_identity.node_name,
                "namespace": results_identity.namespace,
                "image_version": results_identity.image_version,
        },
    )
    return ResultAck(
        run_id=submission.run_id,
        request_id=submission.request_id,
        result_received_at_utc=received_at,
        result_stored_at_utc=stored_at,
        results_identity=results_identity,
    )


@app.get("/v1/results/{request_id}", response_model=StoredResult)
def get_result(request_id: UUID) -> StoredResult:
    with _lock:
        record = _index.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="result not found")
    return record
