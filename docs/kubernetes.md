# Optional Kubernetes adapter

The adapter is implemented and API-mock tested. A live cluster is not part of the checked-in baseline verification.

## Operator configuration

Run one Sentinel instance per configured external tenant/deployment. Start in authenticated mode and set:

| Variable | Meaning |
| --- | --- |
| `KUBE_TENANT` | The tenant whose operator token may submit external alerts |
| `KUBE_NAMESPACE` | Namespace, default `sentinel-lab` |
| `KUBE_DEPLOYMENT` | Deployment, default `checkout` |
| `KUBE_API_URL` | API server, default in-cluster Kubernetes URL |
| `KUBE_TOKEN_PATH` | Mounted service-account token path |
| `KUBE_CA_PATH` | Mounted cluster CA path |
| `KUBE_STABLE_TEMPLATE` | Operator-controlled JSON file containing the known-good `spec.template` |
| `TELEMETRY_URL` | A trusted normalized telemetry gateway |
| `TELEMETRY_TOKEN` | Gateway bearer token |

Capture a known-good PodTemplateSpec before introducing a regression:

```bash
kubectl get deployment checkout -n sentinel-lab -o json \
  | python -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["spec"]["template"]))' \
  > stable-template.json
```

Keep this file immutable for the life of an approval. Changing it invalidates the target digest and execution refuses to proceed. The Role in `infra/kubernetes/rollback-rbac.yaml` is restricted to the configured checkout deployment. Create the namespace separately and bind the executor ServiceAccount to the application pod.

The baseline policy understands `DB_POOL_SIZE` and `RETAIN_BUFFERS` literal environment values in the first container. Other release changes are still visible as template digests but require policy/evidence extensions. The template must match the same deployment labels and workload assumptions; this prototype does not validate arbitrary customer templates.

## Gateway contract

Provide authenticated GET endpoints `/telemetry/metrics`, `/telemetry/logs`, `/telemetry/traces` matching the normalized objects produced by `sandbox.py`. Metrics need `id`, `timestamp`, `error_rate`, `p95_ms`, `window_requests`, `pool_utilization`, `memory_mb`, `memory_growth_mb`, and `dependency_latency_ms`. Logs and traces need evidence IDs plus bounded `entries` / `spans` arrays. A gateway may adapt Prometheus, Datadog, or OTLP stores, but those vendor adapters are not bundled.

## Alert ingress

Submit an authenticated POST to `/api/alerts`:

```json
{"title":"Checkout failures after deployment","source_key":"monitoring-alert-123"}
```

No payload field can supply a URL, namespace, credential, or executable command. Repeated keys with the same alert are deduplicated; conflicting alert bodies return 409.

## Execution and recovery

The captured deployment resourceVersion is bound into the plan. Execution GETs the configured deployment, rejects a changed version, then applies a JSON Patch that tests the same resourceVersion before replacing `spec.template` with the pinned template. A persistent `sentinel.ops/operation` annotation lets the connector recognize a successful retry after a worker crash.

Verification checks the resulting template digest, Kubernetes observed generation and replica availability, and two telemetry samples. Rollout convergence is given a 45-second budget before recovery checks. A slow or unhealthy rollout escalates; it is not silently marked resolved.

Test this in a disposable namespace with a representative telemetry gateway before connecting an actual customer service.
