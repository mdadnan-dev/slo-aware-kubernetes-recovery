from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "k8s" / "base"
EXPECTED_DEPLOYMENTS = {"ingestion", "processing", "results"}


def load_resources() -> list[dict]:
    resources: list[dict] = []
    for path in sorted(BASE.glob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        with path.open(encoding="utf-8") as handle:
            resources.extend(item for item in yaml.safe_load_all(handle) if item)
    return resources


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    resources = load_resources()
    kinds = {item.get("kind") for item in resources}
    if "HorizontalPodAutoscaler" in kinds:
        fail("base manifests must not contain an HPA")
    deployments = {item["metadata"]["name"]: item for item in resources if item.get("kind") == "Deployment"}
    if set(deployments) != EXPECTED_DEPLOYMENTS:
        fail(f"expected deployments {sorted(EXPECTED_DEPLOYMENTS)}, found {sorted(deployments)}")
    services = {item["metadata"]["name"] for item in resources if item.get("kind") == "Service"}
    if services != EXPECTED_DEPLOYMENTS:
        fail(f"expected services {sorted(EXPECTED_DEPLOYMENTS)}, found {sorted(services)}")
    for name, deployment in deployments.items():
        if deployment["metadata"].get("namespace") != "net470-v3":
            fail(f"{name}: wrong namespace")
        if deployment["spec"].get("replicas") != 1:
            fail(f"{name}: qualification deployment must have one replica")
        pod_spec = deployment["spec"]["template"]["spec"]
        if pod_spec.get("automountServiceAccountToken") is not False:
            fail(f"{name}: service account token must not be mounted")
        container = pod_spec["containers"][0]
        if container.get("imagePullPolicy") != "IfNotPresent":
            fail(f"{name}: unexpected pull policy")
        if ":q1" not in container.get("image", ""):
            fail(f"{name}: image must use q1 qualification tag")
        if "requests" not in container.get("resources", {}):
            fail(f"{name}: resource requests are required")
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            if probe not in container:
                fail(f"{name}: missing {probe}")
        security = container.get("securityContext", {})
        if security.get("allowPrivilegeEscalation") is not False or security.get("readOnlyRootFilesystem") is not True:
            fail(f"{name}: container security context is incomplete")
    template = (ROOT / "k8s" / "hpa" / "processing-hpa.template.yaml").read_text(encoding="utf-8")
    if "__TARGET_CPU_UTILIZATION__" not in template:
        fail("HPA template is not gated")
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    compose_services = compose.get("services", {})
    if set(compose_services) != EXPECTED_DEPLOYMENTS:
        fail("Docker Compose must define exactly ingestion, processing and results")
    for name, service in compose_services.items():
        if service.get("image") != f"net470-v3-{name}:q1":
            fail(f"{name}: Docker Compose image identity is incorrect")
        if service.get("read_only") is not True or "healthcheck" not in service:
            fail(f"{name}: Docker Compose hardening or healthcheck is missing")
    for name in EXPECTED_DEPLOYMENTS:
        dockerfile = (ROOT / "services" / name / "Dockerfile").read_text(encoding="utf-8")
        required_fragments = (
            "python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c",
            "runtime-lock.txt",
            "USER 10001:10001",
            "--workers\", \"1",
        )
        for fragment in required_fragments:
            if fragment not in dockerfile:
                fail(f"{name}: Dockerfile is missing required fragment {fragment!r}")
        if ":latest" in dockerfile:
            fail(f"{name}: Dockerfile must not use a latest tag")
    print(
        f"PASS: validated {len(resources)} Kubernetes resources, three Dockerfiles, "
        "Docker Compose topology and gated HPA template"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
