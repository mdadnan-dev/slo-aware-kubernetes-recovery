"""Authoritative Kubernetes HPA-state watcher."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from telemetry.contracts import TelemetryValidationError
from telemetry.writers import (
    AppendOnlyTelemetryWriter,
    TelemetryWriterError,
)


class HpaStateWatcherError(RuntimeError):
    """Raised when authoritative HPA state cannot be observed."""


def _format_utc(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise HpaStateWatcherError(
            "watcher clock must return datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise HpaStateWatcherError(
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


class HpaStateWatcher:
    """Record each new HPA resource version at most once."""

    stream_id = "hpa_state"
    writer_name = "hpa_watcher"

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        episode_id: str,
        namespace: str,
        hpa_name: str,
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
            ("hpa_name", hpa_name),
            ("source_version", source_version),
            ("expected_context", expected_context),
        ):
            if not isinstance(value, str) or not value.strip():
                raise HpaStateWatcherError(
                    f"{field_name} is empty"
                )

        self.run_id = run_id
        self.episode_id = episode_id
        self.namespace = namespace
        self.hpa_name = hpa_name
        self.writer_instance_id = writer_instance_id
        self.source_version = source_version
        self.expected_context = expected_context
        self.kubectl = kubectl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._command_runner = command_runner or _default_runner

        self._seen_resource_versions: set[
            tuple[str, str]
        ] = set()
        self._last_desired_replicas: int | None = None

        self.last_list_resource_version: str | None = None
        self.hpa_present = False

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
            raise HpaStateWatcherError(
                f"kubectl command failed: {message}"
            )

        return completed.stdout.strip()

    def _json(self, *arguments: str) -> dict[str, Any]:
        text = self._run(*arguments)

        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HpaStateWatcherError(
                "kubectl returned invalid JSON"
            ) from exc

        if not isinstance(value, dict):
            raise HpaStateWatcherError(
                "kubectl JSON result is not an object"
            )

        return value

    def _verify_context(self) -> None:
        actual_context = self._run(
            "config",
            "current-context",
        )

        if actual_context != self.expected_context:
            raise HpaStateWatcherError(
                "current Kubernetes context differs "
                "from expected context"
            )

    def sample(self) -> list[dict[str, Any]]:
        self._verify_context()

        hpa_path = (
            "/apis/autoscaling/v2/"
            f"namespaces/{self.namespace}/"
            "horizontalpodautoscalers"
        )

        hpa_list = self._json(
            "get",
            "--raw",
            hpa_path,
        )

        list_metadata = hpa_list.get("metadata", {})
        self.last_list_resource_version = str(
            list_metadata.get("resourceVersion", "")
        )

        selected = next(
            (
                item
                for item in hpa_list.get("items", [])
                if item.get("metadata", {}).get("name")
                == self.hpa_name
            ),
            None,
        )

        if selected is None:
            self.hpa_present = False
            return []

        self.hpa_present = True

        metadata = selected.get("metadata", {})
        spec = selected.get("spec", {})
        status = selected.get("status", {})

        hpa_uid = str(metadata.get("uid", ""))
        resource_version = str(
            metadata.get("resourceVersion", "")
        )

        if not hpa_uid or not resource_version:
            raise HpaStateWatcherError(
                "HPA UID or resource version is missing"
            )

        observation_key = (
            hpa_uid,
            resource_version,
        )

        if observation_key in self._seen_resource_versions:
            return []

        target = spec.get("scaleTargetRef", {})
        target_api_version = str(
            target.get("apiVersion", "")
        )
        target_kind = str(target.get("kind", ""))
        target_name = str(target.get("name", ""))

        if (
            target_api_version != "apps/v1"
            or target_kind != "Deployment"
            or not target_name
        ):
            raise HpaStateWatcherError(
                "HPA scale target is not an apps/v1 Deployment"
            )

        deployment = self._json(
            "get",
            "deployment",
            target_name,
            "-n",
            self.namespace,
            "-o",
            "json",
        )

        deployment_uid = str(
            deployment.get("metadata", {}).get(
                "uid",
                "",
            )
        )

        if not deployment_uid:
            raise HpaStateWatcherError(
                "target deployment UID is missing"
            )

        current_replicas = status.get(
            "currentReplicas",
            0,
        )
        desired_replicas = status.get(
            "desiredReplicas",
            0,
        )

        if not isinstance(current_replicas, int):
            raise HpaStateWatcherError(
                "HPA currentReplicas is not an integer"
            )

        if not isinstance(desired_replicas, int):
            raise HpaStateWatcherError(
                "HPA desiredReplicas is not an integer"
            )

        previous_desired_replicas = (
            self._last_desired_replicas
        )

        desired_replicas_changed = (
            previous_desired_replicas is not None
            and desired_replicas
            != previous_desired_replicas
        )

        record = {
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "hpa_uid": hpa_uid,
            "deployment_uid": deployment_uid,
            "resource_version": resource_version,
            "observed_at_utc": _format_utc(
                self._clock()
            ),
            "writer_instance_id": (
                self.writer_instance_id
            ),
            "source_version": self.source_version,
            "namespace": self.namespace,
            "hpa_name": self.hpa_name,
            "hpa_list_resource_version": (
                self.last_list_resource_version
            ),
            "generation": metadata.get("generation"),
            "observed_generation": status.get(
                "observedGeneration"
            ),
            "target_api_version": target_api_version,
            "target_kind": target_kind,
            "target_name": target_name,
            "minimum_replicas": spec.get(
                "minReplicas",
                1,
            ),
            "maximum_replicas": spec.get(
                "maxReplicas"
            ),
            "current_replicas": current_replicas,
            "desired_replicas": desired_replicas,
            "previous_desired_replicas": (
                previous_desired_replicas
            ),
            "desired_replicas_changed": (
                desired_replicas_changed
            ),
            "scale_up_requested": (
                desired_replicas > current_replicas
            ),
            "last_scale_time": status.get(
                "lastScaleTime"
            ),
            "spec_metrics": spec.get("metrics", []),
            "current_metrics": status.get(
                "currentMetrics",
                [],
            ),
            "conditions": status.get(
                "conditions",
                [],
            ),
        }

        try:
            normalized = self._writer.append(record)
        except (
            TelemetryValidationError,
            TelemetryWriterError,
        ) as exc:
            raise HpaStateWatcherError(
                f"HPA state rejected: {exc}"
            ) from exc

        self._seen_resource_versions.add(
            observation_key
        )
        self._last_desired_replicas = (
            desired_replicas
        )

        return [normalized]

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
