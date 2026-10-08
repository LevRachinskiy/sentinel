# Security model and operational limits

## Implemented boundaries

- Outside explicit demo mode, startup requires configured bearer-token records.
- Server configuration assigns tenant, actor, and role. A client cannot promote itself with headers.
- All incident, approval, and event reads are tenant-scoped.
- Viewer credentials can read evidence; only operator credentials can create incidents or decide plans.
- Approval binds the exact plan digest and expected deployment revision. Duplicate approval enqueues one execution.
- MCP exposes only four read operations against captured incident evidence.
- Provider output cannot call execution tools. Telemetry is presented as untrusted data.
- An independent policy prevents model text from forcing unsupported rollback.
- Infrastructure addresses and credentials come from operator configuration, not incident payloads.
- External HTTP clients disable redirects. Infrastructure connectors ignore ambient proxy variables.
- Kubernetes uses CA verification, mounted service-account tokens, one configured namespace/deployment, and a pinned stable PodTemplate.
- The sample Kubernetes RBAC role grants only get/patch for Deployment `checkout`.
- The sandbox rollback uses an atomic revision precondition and persistent idempotency record.
- Audit entries are linked with SHA-256 and verified against the stored tenant head.

## What these boundaries do not guarantee

Anonymous local demo mode makes every local visitor an operator. The demo ports bind to loopback. A production reverse proxy must never expose that mode.

Static credentials are suitable for an inspectable prototype. They are not OIDC, MFA, session management, credential rotation, or separation of approver and executor identities. An operator can approve their own request. Four-eyes approval and organization-specific permissions are not implemented.

Tenant filtering is an application boundary. Database row-level security and separate customer encryption keys are not implemented. The configured Kubernetes connector supports one external tenant/deployment per Sentinel instance; this is not a general multi-customer control plane.

A database administrator can recompute both the event chain and its head. External anchoring or WORM storage is needed for stronger integrity. Audit append-only behavior is enforced by the application API, not by a write-restricted database role. The verifier detects mutation/truncation against the current stored head.

Provider calls can transmit telemetry outside the deployment. Logs must be curated and redacted by the telemetry gateway before enabling a provider. This version does not implement a comprehensive PII or secret redactor. API keys are not inserted into agent prompts or stored in incident evidence.

The Kubernetes Role permits patching the entire configured Deployment. The application limits patches to its pinned template and operation annotation, but Kubernetes RBAC cannot express that field-level restriction. Admission policy is required to enforce the same limit independently of the application.

The normalized telemetry gateway is trusted for schema and freshness. Network policy, endpoint authentication, rate limiting, request-size limits, retention policy, and data provenance need deployment-specific hardening. Do not point the optional adapter at important workloads before separately validating it in a disposable cluster.
