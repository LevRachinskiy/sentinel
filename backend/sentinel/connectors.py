import asyncio
import copy
import time
from typing import Protocol

import httpx


class Connector(Protocol):
    async def read(self, tool: str) -> dict: ...
    async def rollback(self, key: str, expected_revision: str, target_revision: str) -> dict: ...
    async def sample(self) -> dict: ...


READ_TOOLS = {
    "get_deployment_diff": "deployment",
    "query_metrics": "metrics",
    "query_logs": "logs",
    "query_traces": "traces",
}


class FixtureConnector:
    def __init__(self, evidence):
        self.evidence = copy.deepcopy(evidence)
        self.actions = {}

    async def read(self, tool):
        if tool not in READ_TOOLS:
            raise ValueError("Tool is not in read allowlist")
        return copy.deepcopy(self.evidence[READ_TOOLS[tool]])

    async def rollback(self, key, expected_revision, target_revision):
        if key in self.actions:
            return self.actions[key]
        deploy = self.evidence["deployment"]
        if deploy["revision"] != expected_revision:
            if deploy["revision"] == target_revision:
                return {"revision": target_revision, "already_applied": True}
            raise ValueError("Deployment revision changed; re-investigate")
        deploy["revision"] = target_revision
        if not self.evidence.get("stubborn"):
            self.evidence["metrics"].update(
                error_rate=0, p95_ms=110, memory_growth_mb=0, pool_utilization=0.2, memory_mb=32
            )
        self.actions[key] = {"revision": target_revision, "idempotency_key": key}
        return self.actions[key]

    async def sample(self):
        return await self.read("query_metrics")


class SandboxConnector:
    """Only a configured deployment sandbox URL, never an incident-supplied URL."""

    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    async def request(self, method, path, body=None):
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                    response = await client.request(
                        method, self.url + path, headers={"Authorization": f"Bearer {self.token}"}, json=body
                    )
                    response.raise_for_status()
                    return response.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    raise
                if attempt == 2:
                    raise
                await asyncio.sleep(0.1 * 2**attempt)

    async def read(self, tool):
        if tool not in READ_TOOLS:
            raise ValueError("Tool is not in read allowlist")
        return await self.request("GET", f"/telemetry/{READ_TOOLS[tool]}")

    async def rollback(self, key, expected_revision, target_revision):
        return await self.request(
            "POST",
            "/admin/rollback",
            {
                "idempotency_key": key,
                "expected_revision": expected_revision,
                "target_revision": target_revision,
            },
        )

    async def sample(self):
        await self.traffic()
        return await self.read("query_metrics")

    async def inject(self, scenario, key):
        return await self.request("POST", "/admin/fault", {"scenario": scenario, "idempotency_key": key})

    async def traffic(self):
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:

            async def one():
                try:
                    return await client.get(self.url + "/checkout")
                except httpx.TransportError:
                    return None

            started = time.monotonic()
            await asyncio.gather(*(one() for _ in range(36)))
            return time.monotonic() - started
