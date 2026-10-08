# Two-minute demo

## 0:00 to 0:20: Set the problem

“This is a checkout service. A release reduces its pool capacity. Under concurrent traffic it starts returning HTTP 500s. Sentinel investigates the evidence and proposes a rollback, but it cannot grant itself permission to execute one.”

Select **Connection pool exhaustion**, enable **Live sandbox**, and run it.

## 0:20 to 0:50: Inspect the evidence

Show the four completed read tools. Open **Evidence** and compare pool capacity with error rate. Show logs and trace IDs from actual failing HTTP requests. Return to the proposed cause.

“The diagnosis cites captured evidence. The baseline is deterministic so this demo is reproducible. I can also run OpenAI or Anthropic against the same observations.”

## 0:50 to 1:15: Explain the approval boundary

Show the target revision and the approval button.

“Policy is outside the model. The operator approves this exact plan digest and revision. The executor checks both again. Retries reuse a stable idempotency key, so a worker crash after a successful external write does not repeat the operation.”

Approve rollback.

## 1:15 to 1:35: Verify, then close

Show recovery samples, zero error rate, and the downloaded postmortem. Open the timeline to show the actor and event chain.

“Applying a rollback is not proof of recovery. This incident resolves only after the target revision and configured health checks pass.”

## 1:35 to 2:00: Show the counterexample

Run **Dependency timeout**. It escalates rather than rolling back checkout. Show the replay evaluation and its synthetic-data disclosure.

“I also test negative cases. This suite includes healthy services, incomplete telemetry, and hostile log instructions. The checked-in score is a regression result on synthetic fixtures, not a real-world model accuracy claim.”

## Interview discussion

- What happens if the external write succeeds but the worker dies before commit?
- Why is approval tied to a revision and a digest rather than a Boolean field?
- When does a causal deployment signal justify rollback, and when should the agent abstain?
- What database transaction replaces the queue dual-write problem?
- What does the audit chain detect, and what can a database administrator still change?
- How would native customer integrations and held-out real incidents change the evaluation?
