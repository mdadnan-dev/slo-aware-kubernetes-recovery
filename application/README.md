# NET 470 V3 clean-start microservices

This repository is the fresh three-service application for the research project
SLO-Aware Recovery-Stage-Adaptive Control for Kubernetes Microservices under
Dynamic Workloads. It is qualification software, not formal experimental
evidence. All legacy scripts, manifests, workloads, thresholds and results are
excluded.

## Architecture

`qualification client -> ingestion -> processing -> results`

- **Ingestion** accepts a request, assigns the entry timestamp and forwards it.
- **Processing** performs deterministic CPU work. It is the only service that
  may receive an HPA after G4 passes.
- **Results** stores a bounded in-memory record and returns its timestamps.

Every completed response contains the request and run identities, UTC stage
timestamps, and the pod name, pod UID, node, namespace and image version for
all three services. The base system intentionally has no database, broker,
cache, ingress controller, service mesh or HPA because those would add
unmeasured recovery stages.

## Mandatory research boundary

1. Run G0 before using this repository.
2. Complete G1A before building or deploying the application.
3. Use the `q1` images and one replica per service for G1B.
4. Treat `25000` work units and all base resource requests as qualification-only
   provisional values. They are not an SLO, stress workload or calibrated
   experiment setting.
5. Do not generate or apply an HPA until G4 passes. The HPA file contains
   non-applicable placeholders on purpose.
6. Preserve failed attempts and use a new attempt ID after a mandatory failure.

## Windows PowerShell quick path

From `C:\net470-v3\application`:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\00-preflight.ps1

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\requirements\dev.txt

python -m compileall .\common .\services .\tools
python -m pytest
python .\tools\validate_project.py

.\scripts\01-build-images.ps1
.\scripts\02-compose-smoke.ps1
.\scripts\03-deploy-kubernetes.ps1
.\scripts\04-kubernetes-smoke.ps1
.\scripts\05-capture-application-state.ps1
```

The Kubernetes scripts assume that the locked G1A procedure has already
created and qualified the `net470-v3` Minikube profile and that kubectl can
reach it. The deployment script loads the locally built immutable image
content into Minikube and applies only `k8s/base`.

## Manual verification

```powershell
kubectl get deployment,pod,service,endpoints -n net470-v3 -o wide
kubectl get hpa -n net470-v3
kubectl top pods -n net470-v3 --containers
kubectl port-forward service/ingestion 8001:8000 -n net470-v3
```

In a second PowerShell window:

```powershell
$body = @{
  run_id = "V3-Q-G0G1-A01-MANUAL"
  work_units = 25000
  payload = "qualification"
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8001/v1/jobs `
  -ContentType application/json `
  -Body $body | ConvertTo-Json -Depth 12
```

PASS requires `status=completed`, all seven server-side timestamps, all three
service identities, one Ready pod per Deployment, non-empty Service endpoints,
working `kubectl top`, and no HPA.

## Resource qualification and HPA gates

After fresh single-pod observations, generate a reviewed resource-request patch:

```powershell
.\scripts\06-render-qualified-resources.ps1 `
  -IngestionCpuRequest <measured-value> `
  -IngestionMemoryRequest <measured-value> `
  -ProcessingCpuRequest <measured-value> `
  -ProcessingMemoryRequest <measured-value> `
  -ResultsCpuRequest <measured-value> `
  -ResultsMemoryRequest <measured-value>

kubectl apply -k .\k8s\qualified
```

The generated patch must be reviewed, justified, hashed and frozen before G1B
is signed. Do not type literal placeholders into the command.

Only after documented G4 PASS may `07-render-hpa.ps1` be run with fresh
calibrated values and `-ConfirmG4Passed`. The script generates a manifest but
does not apply it.

## Qualification exit criteria

- Source compiles and all unit/API tests pass.
- Docker builds are clean and image identities are captured.
- Docker Compose completes one end-to-end request.
- Kubernetes runs exactly one Ready pod for each service.
- All Services have endpoints and cluster DNS works through the request chain.
- Three qualification cases complete without a missing or mismatched identity.
- Runtime image IDs, package versions, logs, manifests and source hashes are
  captured in the current attempt's evidence directory.
- Qualified resource requests are based on fresh observations.
- No HPA, experimental workload, SLO threshold or controller rule is active.

Only then may G1B be signed and the G0/G1 freeze procedure be completed. G2
telemetry qualification is the next permitted stage.

