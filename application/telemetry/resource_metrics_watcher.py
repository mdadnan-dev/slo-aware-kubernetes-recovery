"""Authoritative Kubernetes resource-metrics watcher."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Self

from telemetry.contracts import TelemetryValidationError
from telemetry.writers import (
    AppendOnlyTelemetryWriter,
    TelemetryWriterError,
)


class ResourceMetricsWatcherError(RuntimeError):
    """Raised when an authoritative resource sample cannot be recorded."""


def _format_utc(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise ResourceMetricsWatcherError(
            "watcher clock must return datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ResourceMetricsWatcherError(
            "watcher clock must return UTC datetime"
        )

    return value.isoformat().replace("+00:00", "Z")


def cpu_quantity_to_nanocores(value: str) -> int:
    """Convert a Kubernetes CPU quantity into integer nanocores."""

    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)(n|u|m)?",
        str(value).strip(),
    )

    if match is None:
        raise ResourceMetricsWatcherError(
            f"unsupported CPU quantity: {value}"
        )

    factors = {
        "n": Decimal(1),
        "u": Decimal(1_000),
        "m": Decimal(1_000_000),
        "": Decimal(1_000_000_000),
    }

    try:
        return int(
            Decimal(match.group(1))
            * factors[match.group(2) or ""]
        )
    except InvalidOperation as exc:
        raise ResourceMetricsWatcherError(
            f"invalid CPU quantity: {value}"
        ) from exc


def memory_quantity_to_bytes(value: str) -> int:
    """Convert common Kubernetes memory quantities into bytes."""

    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?",
        str(value).strip(),
    )

    if match is None:
        raise ResourceMetricsWatcherError(
            f"unsupported memory quantity: {value}"
        )

    factors = {
        "": Decimal(1),
        "Ki": Decimal(1024),
        "Mi": Decimal(1024 ** 2),
        "Gi": Decimal(1024 ** 3),
        "Ti": Decimal(1024 ** 4),
        "K": Decimal(1000),
        "M": Decimal(1000 ** 2),
        "G": Decimal(1000 ** 3),
        "T": Decimal(1000 ** 4),
    }

    try:
        return int(
            Decimal(match.group(1))
            * factors[match.group(2) or ""]
        )
    except InvalidOperation as exc:
        raise ResourceMetricsWatcherError(
            f"invalid memory quantity: {value}"
        ) from exc


def _default_runner(
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


class ResourceMetricsWatcher:
    """Collect one Metrics API sample for each selected pod."""

    stream_id = "resource_metrics"
    writer_name = "metrics_watcher"

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        episode_id: str,
        namespace: str,
        deployment_name: str,
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
            ("deployment_name", deployment_name),
            ("container_name", container_name),
            ("source_version", source_version),
            ("expected_context", expected_context),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ResourceMetricsWatcherError(
                    f"{field_name} is empty"
                )

        self.run_id = run_id
        self.episode_id = episode_id
        self.namespace = namespace
        self.deployment_name = deployment_name
        self.container_name = container_name
        self.writer_instance_id = writer_instance_id
        self.source_version = source_version
        self.expected_context = expected_context
        self.kubectl = kubectl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._command_runner = command_runner or _default_runner

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

    def _run(self, *arguments: str) -> str:
        command = [self.kubectl, *arguments]
        completed = self._command_runner(command)

        if completed.returncode != 0:
            message = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "unknown kubectl failure"
            )
            raise ResourceMetricsWatcherError(
                f"kubectl command failed: {message}"
            )

        return completed.stdout.strip()

    def _json(self, *arguments: str) -> dict[str, Any]:
        text = self._run(*arguments)

        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ResourceMetricsWatcherError(
                "kubectl returned invalid JSON"
            ) from exc

        if not isinstance(value, dict):
            raise ResourceMetricsWatcherError(
                "kubectl JSON result is not an object"
            )

        return value

    def _verify_context(self) -> None:
        actual_context = self._run(
            "config",
            "current-context",
        )

        if actual_context != self.expected_context:
            raise ResourceMetricsWatcherError(
                "current Kubernetes context differs "
                "from expected context"
            )

    def sample(self) -> list[dict[str, Any]]:
        self._verify_context()

        deployment = self._json(
            "get",
            "deployment",
            self.deployment_name,
            "-n",
            self.namespace,
            "-o",
            "json",
        )

        metadata = deployment.get("metadata", {})
        deployment_uid = str(metadata.get("uid", ""))
        deployment_resource_version = str(
            metadata.get("resourceVersion", "")
        )

        match_labels = (
            deployment.get("spec", {})
            .get("selector", {})
            .get("matchLabels", {})
        )

        if (
            not deployment_uid
            or not isinstance(match_labels, dict)
            or not match_labels
        ):
            raise ResourceMetricsWatcherError(
                "deployment identity or selector is missing"
            )

        selector = ",".join(
            f"{key}={value}"
            for key, value in sorted(match_labels.items())
        )

        pod_list = self._json(
            "get",
            "pods",
            "-n",
            self.namespace,
            "-l",
            selector,
            "-o",
            "json",
        )

        pod_lookup: dict[str, dict[str, Any]] = {}

        for pod in pod_list.get("items", []):
            pod_metadata = pod.get("metadata", {})
            pod_spec = pod.get("spec", {})
            pod_name = str(pod_metadata.get("name", ""))
            pod_uid = str(pod_metadata.get("uid", ""))

            container_spec = next(
                (
                    container
                    for container in pod_spec.get(
                        "containers",
                        [],
                    )
                    if container.get("name")
                    == self.container_name
                ),
                None,
            )

            if pod_name and pod_uid and container_spec:
                pod_lookup[pod_name] = {
                    "pod_uid": pod_uid,
                    "pod_resource_version": str(
                        pod_metadata.get(
                            "resourceVersion",
                            "",
                        )
                    ),
                    "node_name": str(
                        pod_spec.get("nodeName", "")
                    ),
                    "container_spec": container_spec,
                }

        if not pod_lookup:
            raise ResourceMetricsWatcherError(
                "no selected pods contain the target container"
            )

        metrics_path = (
            "/apis/metrics.k8s.io/v1beta1/"
            f"namespaces/{self.namespace}/pods"
        )

        metrics = self._json(
            "get",
            "--raw",
            metrics_path,
        )

        metrics_resource_version = str(
            metrics.get("metadata", {}).get(
                "resourceVersion",
                "",
            )
        )

        resource_version_available = bool(
            metrics_resource_version
        )

        if not metrics_resource_version:
            metrics_resource_version = (
                "NOT_PROVIDED_BY_METRICS_API"
            )

        written_at_utc = _format_utc(self._clock())
        records: list[dict[str, Any]] = []

        for pod_metrics in metrics.get("items", []):
            metric_metadata = pod_metrics.get(
                "metadata",
                {},
            )
            pod_name = str(
                metric_metadata.get("name", "")
            )

            if pod_name not in pod_lookup:
                continue

            container_metrics = next(
                (
                    container
                    for container in pod_metrics.get(
                        "containers",
                        [],
                    )
                    if container.get("name")
                    == self.container_name
                ),
                None,
            )

            if container_metrics is None:
                raise ResourceMetricsWatcherError(
                    f"metrics missing target container: {pod_name}"
                )

            usage = container_metrics.get("usage", {})
            cpu_usage = str(usage.get("cpu", ""))
            memory_usage = str(
                usage.get("memory", "")
            )

            if not cpu_usage or not memory_usage:
                raise ResourceMetricsWatcherError(
                    f"incomplete metrics for pod: {pod_name}"
                )

            pod_info = pod_lookup[pod_name]
            container_spec = pod_info["container_spec"]
            requests = (
                container_spec.get("resources", {})
                .get("requests", {})
            )

            cpu_request = str(
                requests.get("cpu", "")
            )
            memory_request = str(
                requests.get("memory", "")
            )

            cpu_usage_nanocores = (
                cpu_quantity_to_nanocores(cpu_usage)
            )
            memory_usage_bytes = (
                memory_quantity_to_bytes(memory_usage)
            )

            cpu_request_nanocores = (
                cpu_quantity_to_nanocores(cpu_request)
                if cpu_request
                else None
            )

            memory_request_bytes = (
                memory_quantity_to_bytes(memory_request)
                if memory_request
                else None
            )

            cpu_utilization_percent = (
                round(
                    cpu_usage_nanocores
                    * 100
                    / cpu_request_nanocores,
                    6,
                )
                if cpu_request_nanocores
                else None
            )

            record = {
                "run_id": self.run_id,
                "episode_id": self.episode_id,
                "sample_time_utc": str(
                    pod_metrics.get("timestamp", "")
                ),
                "deployment_uid": deployment_uid,
                "pod_uid": pod_info["pod_uid"],
                "written_at_utc": written_at_utc,
                "writer_instance_id": (
                    self.writer_instance_id
                ),
                "source_version": self.source_version,
                "metrics_api_resource_version": (
                    metrics_resource_version
                ),
                "metrics_api_resource_version_available": (
                    resource_version_available
                ),
                "deployment_name": self.deployment_name,
                "deployment_resource_version": (
                    deployment_resource_version
                ),
                "namespace": self.namespace,
                "pod_name": pod_name,
                "pod_resource_version": (
                    pod_info["pod_resource_version"]
                ),
                "node_name": pod_info["node_name"],
                "container_name": self.container_name,
                "metrics_window": str(
                    pod_metrics.get("window", "")
                ),
                "cpu_usage": cpu_usage,
                "cpu_usage_nanocores": (
                    cpu_usage_nanocores
                ),
                "cpu_request": cpu_request,
                "cpu_request_nanocores": (
                    cpu_request_nanocores
                ),
                "cpu_utilization_percent": (
                    cpu_utilization_percent
                ),
                "memory_usage": memory_usage,
                "memory_usage_bytes": memory_usage_bytes,
                "memory_request": memory_request,
                "memory_request_bytes": (
                    memory_request_bytes
                ),
            }

            try:
                records.append(
                    self._writer.append(record)
                )
            except (
                TelemetryValidationError,
                TelemetryWriterError,
            ) as exc:
                raise ResourceMetricsWatcherError(
                    f"resource metric rejected: {exc}"
                ) from exc

        if not records:
            raise ResourceMetricsWatcherError(
                "Metrics API returned no selected pod samples"
            )

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
