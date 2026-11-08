"""Authoritative Kubernetes pod-lifecycle watcher for T3-T6."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote

from telemetry.contracts import TelemetryValidationError
from telemetry.writers import (
    AppendOnlyTelemetryWriter,
    TelemetryWriterError,
)


LIFECYCLE_EVENT_IDS = {
    "T3_POD_CREATED": "T3",
    "T4_POD_SCHEDULED": "T4",
    "T5_CONTAINER_STARTED": "T5",
    "T6_POD_READY": "T6",
}


class PodLifecycleWatcherError(RuntimeError):
    """Raised when pod lifecycle evidence cannot be observed."""


def _format_utc(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise PodLifecycleWatcherError(
            "watcher clock must return datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise PodLifecycleWatcherError(
            "watcher clock must return UTC datetime"
        )

    return value.isoformat().replace("+00:00", "Z")


def _default_runner(
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


class PodLifecycleWatcher:
    """Record newly observed T3-T6 lifecycle events once."""

    stream_id = "pod_lifecycle"
    writer_name = "pod_watcher"

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        episode_id: str,
        namespace: str,
        label_selector: str,
        container_name: str,
        writer_instance_id: str,
        source_version: str,
        expected_context: str = "net470-v3",
        kubectl: str = "kubectl",
        clock: Callable[[], datetime] | None = None,
        command_runner: Callable[
            [list[str]],
            subprocess.CompletedProcess[str],
        ] | None = None,
    ) -> None:
        for field_name, value in (
            ("episode_id", episode_id),
            ("namespace", namespace),
            ("label_selector", label_selector),
            ("container_name", container_name),
            ("source_version", source_version),
            ("expected_context", expected_context),
        ):
            if not isinstance(value, str) or not value.strip():
                raise PodLifecycleWatcherError(
                    f"{field_name} is empty"
                )

        self.run_id = run_id
        self.episode_id = episode_id
        self.namespace = namespace
        self.label_selector = label_selector
        self.container_name = container_name
        self.writer_instance_id = writer_instance_id
        self.source_version = source_version
        self.expected_context = expected_context
        self.kubectl = kubectl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._command_runner = command_runner or _default_runner

        self._seen_event_keys: set[
            tuple[str, str, str]
        ] = set()

        self.last_list_resource_version: str | None = None

        self._writer = AppendOnlyTelemetryWriter(
            root,
            stream_id=self.stream_id,
            run_id=run_id,
            writer_name=self.writer_name,
            writer_instance_id=writer_instance_id,
        )

    @property
    def data_path(self) -> Path:
        return self._writer.data_path

    @property
    def ownership_path(self) -> Path:
        return self._writer.ownership_path

    def _run(self, *arguments: str) -> str:
        command = [self.kubectl, *arguments]
        completed = self._command_runner(command)

        if completed.returncode != 0:
            message = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "unknown kubectl failure"
            )
            raise PodLifecycleWatcherError(
                f"kubectl command failed: {message}"
            )

        return completed.stdout.strip()

    def _json(self, *arguments: str) -> dict[str, Any]:
        text = self._run(*arguments)

        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PodLifecycleWatcherError(
                "kubectl returned invalid JSON"
            ) from exc

        if not isinstance(value, dict):
            raise PodLifecycleWatcherError(
                "kubectl JSON result is not an object"
            )

        return value

    def _verify_context(self) -> None:
        actual_context = self._run(
            "config",
            "current-context",
        )

        if actual_context != self.expected_context:
            raise PodLifecycleWatcherError(
                "current Kubernetes context differs "
                "from expected context"
            )

    @staticmethod
    def _condition(
        pod: dict[str, Any],
        condition_type: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                condition
                for condition in (
                    pod.get("status", {})
                    .get("conditions", [])
                )
                if condition.get("type") == condition_type
            ),
            None,
        )

    def sample(self) -> list[dict[str, Any]]:
        self._verify_context()

        encoded_selector = quote(
            self.label_selector,
            safe="=,",
        )

        pod_path = (
            f"/api/v1/namespaces/{self.namespace}/pods"
            f"?labelSelector={encoded_selector}"
        )

        pod_list = self._json(
            "get",
            "--raw",
            pod_path,
        )

        self.last_list_resource_version = str(
            pod_list.get("metadata", {}).get(
                "resourceVersion",
                "",
            )
        )

        observed_at_utc = _format_utc(self._clock())
        records: list[dict[str, Any]] = []

        for pod in pod_list.get("items", []):
            metadata = pod.get("metadata", {})
            spec = pod.get("spec", {})
            status = pod.get("status", {})

            pod_name = str(metadata.get("name", ""))
            pod_uid = str(metadata.get("uid", ""))
            pod_resource_version = str(
                metadata.get("resourceVersion", "")
            )
            creation_time = str(
                metadata.get("creationTimestamp", "")
            )

            if (
                not pod_name
                or not pod_uid
                or not pod_resource_version
                or not creation_time
            ):
                raise PodLifecycleWatcherError(
                    "pod identity, resource version, "
                    "or creation timestamp is missing"
                )

            replica_owner = next(
                (
                    owner
                    for owner in metadata.get(
                        "ownerReferences",
                        [],
                    )
                    if owner.get("kind") == "ReplicaSet"
                ),
                None,
            )

            if replica_owner is None:
                raise PodLifecycleWatcherError(
                    f"ReplicaSet owner missing: {pod_name}"
                )

            replicaset_name = str(
                replica_owner.get("name", "")
            )
            replicaset_uid = str(
                replica_owner.get("uid", "")
            )

            replicaset = self._json(
                "get",
                "replicaset",
                replicaset_name,
                "-n",
                self.namespace,
                "-o",
                "json",
            )

            rs_metadata = replicaset.get("metadata", {})
            actual_replicaset_uid = str(
                rs_metadata.get("uid", "")
            )

            if (
                not replicaset_uid
                or actual_replicaset_uid
                != replicaset_uid
            ):
                raise PodLifecycleWatcherError(
                    "ReplicaSet UID join mismatch"
                )

            deployment_owner = next(
                (
                    owner
                    for owner in rs_metadata.get(
                        "ownerReferences",
                        [],
                    )
                    if owner.get("kind") == "Deployment"
                ),
                None,
            )

            if deployment_owner is None:
                raise PodLifecycleWatcherError(
                    "Deployment owner missing from ReplicaSet"
                )

            deployment_name = str(
                deployment_owner.get("name", "")
            )
            deployment_uid = str(
                deployment_owner.get("uid", "")
            )

            if not deployment_name or not deployment_uid:
                raise PodLifecycleWatcherError(
                    "Deployment join identity is incomplete"
                )

            scheduled = self._condition(
                pod,
                "PodScheduled",
            )
            ready = self._condition(
                pod,
                "Ready",
            )

            container_status = next(
                (
                    container
                    for container in status.get(
                        "containerStatuses",
                        [],
                    )
                    if container.get("name")
                    == self.container_name
                ),
                None,
            )

            running = (
                container_status.get("state", {}).get(
                    "running"
                )
                if container_status is not None
                else None
            )

            event_times: list[tuple[str, str]] = [
                ("T3_POD_CREATED", creation_time)
            ]

            if (
                scheduled is not None
                and scheduled.get("status") == "True"
                and scheduled.get("lastTransitionTime")
            ):
                event_times.append(
                    (
                        "T4_POD_SCHEDULED",
                        str(
                            scheduled[
                                "lastTransitionTime"
                            ]
                        ),
                    )
                )

            if (
                running is not None
                and running.get("startedAt")
            ):
                event_times.append(
                    (
                        "T5_CONTAINER_STARTED",
                        str(running["startedAt"]),
                    )
                )

            if (
                ready is not None
                and ready.get("status") == "True"
                and ready.get("lastTransitionTime")
            ):
                event_times.append(
                    (
                        "T6_POD_READY",
                        str(
                            ready["lastTransitionTime"]
                        ),
                    )
                )

            for event_type, event_time_utc in event_times:
                event_key = (
                    pod_uid,
                    event_type,
                    event_time_utc,
                )

                if event_key in self._seen_event_keys:
                    continue

                record = {
                    "run_id": self.run_id,
                    "episode_id": self.episode_id,
                    "pod_uid": pod_uid,
                    "replicaset_uid": replicaset_uid,
                    "deployment_uid": deployment_uid,
                    "event_type": event_type,
                    "event_id": (
                        LIFECYCLE_EVENT_IDS[event_type]
                    ),
                    "event_time_utc": event_time_utc,
                    "observed_at_utc": observed_at_utc,
                    "writer_instance_id": (
                        self.writer_instance_id
                    ),
                    "source_version": self.source_version,
                    "resource_version": (
                        pod_resource_version
                    ),
                    "pod_list_resource_version": (
                        self.last_list_resource_version
                    ),
                    "namespace": self.namespace,
                    "pod_name": pod_name,
                    "node_name": str(
                        spec.get("nodeName", "")
                    ),
                    "replicaset_name": replicaset_name,
                    "replicaset_resource_version": str(
                        rs_metadata.get(
                            "resourceVersion",
                            "",
                        )
                    ),
                    "deployment_name": deployment_name,
                    "container_name": self.container_name,
                    "container_id": (
                        str(
                            container_status.get(
                                "containerID",
                                "",
                            )
                        )
                        if container_status
                        else ""
                    ),
                    "image": (
                        str(
                            container_status.get(
                                "image",
                                "",
                            )
                        )
                        if container_status
                        else ""
                    ),
                    "image_id": (
                        str(
                            container_status.get(
                                "imageID",
                                "",
                            )
                        )
                        if container_status
                        else ""
                    ),
                }

                try:
                    normalized = self._writer.append(
                        record
                    )
                except (
                    TelemetryValidationError,
                    TelemetryWriterError,
                ) as exc:
                    raise PodLifecycleWatcherError(
                        f"pod lifecycle event rejected: {exc}"
                    ) from exc

                self._seen_event_keys.add(event_key)
                records.append(normalized)

        return records

    def close(self) -> None:
        self._writer.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        self.close()
