# Next milestones

## Customer pilot

1. Use a disposable Kubernetes deployment with a real telemetry gateway, then run the adapter's entire approval and recovery flow.
2. Add one native alert and one native observability integration based on a pilot team's actual stack.
3. Collect anonymized real incidents with permission and create held-out evidence bundles.
4. Evaluate selected model versions against the same held-out cases and report accuracy, abstention, unsafe raw proposals, unsafe policy-approved plans, real recovery outcomes, latency, and actual token cost separately.
5. Record a live demo and publish only measured claims.

## Production engineering

- OIDC, credential rotation, independent approver/executor identities, and organization policy.
- Separate leased worker processes, queue health metrics, fairness, deadlines, and cancellation.
- Schema migrations, backups, data retention, database role restrictions, and optional row-level security.
- Freshness validation for telemetry samples and robust recovery over longer observation windows.
- Native OpenTelemetry exporter/collector configuration and instrumentation of each workflow stage.
- Durable audit anchoring and deployment-enforced admission restrictions.
- Adaptive investigation tools, bounded budgets, circuit breakers, and tool-level evaluation.
- Temporal if workflow complexity exceeds the database queue's operational value; Kafka or Redis if actual event volume justifies them.

These are future work. Their names are not evidence they already exist in this repository.
