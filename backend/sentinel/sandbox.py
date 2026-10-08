"""A real HTTP checkout service with deterministic fault injection, not a cloud executor."""

import asyncio
import hmac
import json
import os
import sqlite3
import statistics
import time
from collections import deque
from contextlib import contextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel, Field

from .models import now


class Fault(BaseModel):
    scenario: str
    idempotency_key: str = Field(min_length=1, max_length=200)


class Rollback(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_revision: str
    target_revision: str


def create_sandbox(path=None, token=None):
    path = path or os.getenv("SANDBOX_DB", "sandbox.db")
    token = token or os.getenv("SANDBOX_TOKEN", "local-sandbox-only")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS actions (key TEXT PRIMARY KEY, result TEXT NOT NULL)")
    initial = {
        "revision": "stable-v1",
        "previous_revision": "stable-v1",
        "scenario": "healthy",
        "deployed_at": now(),
        "pool_size": 32,
        "retain_buffers": False,
    }
    conn.execute("INSERT OR IGNORE INTO state VALUES (1, ?)", (json.dumps(initial),))
    conn.commit()
    conn.close()

    @contextmanager
    def db():
        with sqlite3.connect(path, timeout=10) as c:
            c.execute("BEGIN IMMEDIATE")
            yield c

    def state():
        with sqlite3.connect(path) as c:
            return json.loads(c.execute("SELECT data FROM state WHERE id=1").fetchone()[0])

    runtime = {"active": 0, "memory_mb": 32.0, "memory_start": 32.0}
    samples, logs, spans = deque(maxlen=36), deque(maxlen=100), deque(maxlen=100)
    provider = TracerProvider()
    tracer = provider.get_tracer("sentinel.checkout")
    app = FastAPI(title="Sentinel Checkout Fault Lab")

    def admin(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "Sandbox admin token required")

    def reset_runtime():
        runtime.update(active=0, memory_mb=32.0, memory_start=32.0)
        samples.clear()
        logs.clear()
        spans.clear()

    @app.get("/health")
    def health():
        return {"status": "ok", "revision": state()["revision"]}

    @app.get("/checkout")
    async def checkout():
        current = state()
        started = time.perf_counter()
        status, message, span_name = 200, "checkout succeeded", "checkout.request"
        with tracer.start_as_current_span("checkout") as span:
            span.set_attribute("deployment.revision", current["revision"])
            scenario = current["scenario"]
            if scenario == "pool_exhaustion" and runtime["active"] >= current["pool_size"]:
                await asyncio.sleep(0.08)
                status, message, span_name = 500, "connection pool acquire deadline exceeded", "db.acquire"
            elif scenario == "dependency_timeout":
                await asyncio.sleep(0.35)
                status, message, span_name = 503, "payment dependency timeout", "payment.authorize"
            else:
                runtime["active"] += 1
                try:
                    await asyncio.sleep(0.1)
                    if current["retain_buffers"]:
                        runtime["memory_mb"] += 1.7
                        if runtime["memory_mb"] > 64:
                            status, message = 500, "memory budget exceeded"
                finally:
                    runtime["active"] -= 1
            elapsed = (time.perf_counter() - started) * 1000
            span.set_attribute("http.response.status_code", status)
            context = span.get_span_context()
            trace_id = format(context.trace_id, "032x")
            samples.append({"status": status, "latency_ms": elapsed})
            logs.append(
                {
                    "timestamp": now(),
                    "level": "ERROR" if status >= 500 else "INFO",
                    "message": message,
                    "trace_id": trace_id,
                    "revision": current["revision"],
                }
            )
            spans.append(
                {
                    "timestamp": now(),
                    "name": span_name,
                    "duration_ms": round(elapsed, 2),
                    "status": "ERROR" if status >= 500 else "OK",
                    "trace_id": trace_id,
                }
            )
        if status >= 500:
            raise HTTPException(status, message)
        return {"order_id": trace_id[:12], "revision": current["revision"]}

    @app.post("/admin/fault")
    def fault(body: Fault, _=Depends(admin)):
        if body.scenario not in {"pool_exhaustion", "dependency_timeout", "memory_leak", "healthy"}:
            raise HTTPException(422, "Unsupported live scenario")
        key = "fault:" + body.idempotency_key
        with db() as c:
            prior = c.execute("SELECT result FROM actions WHERE key=?", (key,)).fetchone()
            if prior:
                return json.loads(prior[0])
            updated = {
                "revision": "release-" + body.idempotency_key[:7],
                "previous_revision": "stable-v1",
                "scenario": body.scenario,
                "deployed_at": now(),
                "pool_size": 2 if body.scenario == "pool_exhaustion" else 32,
                "retain_buffers": body.scenario == "memory_leak",
            }
            c.execute("UPDATE state SET data=? WHERE id=1", (json.dumps(updated),))
            c.execute("INSERT INTO actions VALUES (?, ?)", (key, json.dumps(updated)))
        reset_runtime()
        return updated

    @app.post("/admin/rollback")
    def rollback(body: Rollback, _=Depends(admin)):
        with db() as c:
            prior = c.execute("SELECT result FROM actions WHERE key=?", (body.idempotency_key,)).fetchone()
            if prior:
                return json.loads(prior[0])
            current = json.loads(c.execute("SELECT data FROM state WHERE id=1").fetchone()[0])
            if current["revision"] != body.expected_revision:
                raise HTTPException(409, "Revision precondition failed")
            if body.target_revision != "stable-v1":
                raise HTTPException(403, "Only the pinned stable sandbox revision can be restored")
            current.update(revision="stable-v1", scenario="healthy", pool_size=32, retain_buffers=False)
            c.execute("UPDATE state SET data=? WHERE id=1", (json.dumps(current),))
            result = {"revision": "stable-v1", "idempotency_key": body.idempotency_key}
            c.execute("INSERT INTO actions VALUES (?, ?)", (body.idempotency_key, json.dumps(result)))
        reset_runtime()
        return result

    @app.get("/telemetry/{source}")
    def telemetry(source: str, _=Depends(admin)):
        current, timestamp = state(), now()
        if source == "deployment":
            return {
                "id": "deployment-1",
                "source": "deployments",
                "revision": current["revision"],
                "previous_revision": current["previous_revision"],
                "timestamp": current["deployed_at"],
                "rollback_allowed": current["scenario"] in {"pool_exhaustion", "memory_leak"},
                "diff": {
                    "pool_size": {"before": 32, "after": current["pool_size"]},
                    "retain_buffers": {"before": False, "after": current["retain_buffers"]},
                },
            }
        if source == "metrics":
            values = list(samples)
            durations = sorted(v["latency_ms"] for v in values)
            return {
                "id": "metrics-1",
                "source": "metrics",
                "timestamp": timestamp,
                "error_rate": sum(v["status"] >= 500 for v in values) / max(1, len(values)),
                "p95_ms": round(durations[max(0, int(len(durations) * 0.95) - 1)], 2) if durations else 0,
                "mean_ms": round(statistics.mean(durations), 2) if durations else 0,
                "pool_utilization": 1.0 if current["scenario"] == "pool_exhaustion" and values else 0.2,
                "memory_mb": round(runtime["memory_mb"], 2),
                "memory_growth_mb": round(runtime["memory_mb"] - runtime["memory_start"], 2),
                "dependency_latency_ms": 350 if current["scenario"] == "dependency_timeout" else 30,
                "window_requests": len(values),
            }
        if source == "logs":
            return {"id": "logs-1", "source": "logs", "timestamp": timestamp, "entries": list(logs)[-40:]}
        if source == "traces":
            return {"id": "traces-1", "source": "traces", "timestamp": timestamp, "spans": list(spans)[-40:]}
        raise HTTPException(404, "Unknown telemetry source")

    return app
