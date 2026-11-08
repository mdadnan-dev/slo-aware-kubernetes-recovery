from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(timezone.utc)


class PodIdentity(BaseModel):
    service: str
    pod_name: str
    pod_uid: str
    node_name: str
    namespace: str
    image_version: str


class WorkRequest(BaseModel):
    run_id: str = Field(min_length=8, max_length=96, pattern=r"^V3-[QKPR]-[A-Za-z0-9._-]+$")
    request_id: UUID = Field(default_factory=uuid4)
    submitted_at_utc: datetime = Field(default_factory=utc_now)
    work_units: int = Field(default=25_000, ge=1, le=5_000_000)
    payload: str = Field(default="qualification", min_length=1, max_length=256)

    @field_validator("submitted_at_utc")
    @classmethod
    def normalize_submitted_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ProcessingRequest(WorkRequest):
    ingestion_received_at_utc: datetime
    ingestion_identity: PodIdentity

    @field_validator("ingestion_received_at_utc")
    @classmethod
    def normalize_ingestion_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ResultSubmission(BaseModel):
    run_id: str
    request_id: UUID
    work_units: int
    payload: str
    checksum: str
    submitted_at_utc: datetime
    ingestion_received_at_utc: datetime
    processing_received_at_utc: datetime
    processing_started_at_utc: datetime
    processing_completed_at_utc: datetime
    ingestion_identity: PodIdentity
    processing_identity: PodIdentity

    @field_validator(
        "submitted_at_utc",
        "ingestion_received_at_utc",
        "processing_received_at_utc",
        "processing_started_at_utc",
        "processing_completed_at_utc",
    )
    @classmethod
    def normalize_result_times(cls, value: datetime) -> datetime:
        return require_utc(value)


class ResultAck(BaseModel):
    status: Literal["stored"] = "stored"
    run_id: str
    request_id: UUID
    result_received_at_utc: datetime
    result_stored_at_utc: datetime
    results_identity: PodIdentity


class StageTimestamps(BaseModel):
    submitted_at_utc: datetime
    ingestion_received_at_utc: datetime
    processing_received_at_utc: datetime
    processing_started_at_utc: datetime
    processing_completed_at_utc: datetime
    result_received_at_utc: datetime
    result_stored_at_utc: datetime
    ingestion_response_at_utc: datetime | None = None


class IdentityChain(BaseModel):
    ingestion: PodIdentity
    processing: PodIdentity
    results: PodIdentity


class WorkResponse(BaseModel):
    status: Literal["completed"] = "completed"
    run_id: str
    request_id: UUID
    work_units: int
    checksum: str
    timestamps: StageTimestamps
    identities: IdentityChain


class StoredResult(BaseModel):
    submission: ResultSubmission
    result_received_at_utc: datetime
    result_stored_at_utc: datetime
    results_identity: PodIdentity

