import asyncio

import httpx
import pytest
from sentinel.sandbox import create_sandbox


def test_live_pool_failure_and_persistent_idempotent_rollback(tmp_path):
    path = str(tmp_path / "sandbox.db")
    app = create_sandbox(path, "secret")
    headers = {"Authorization": "Bearer secret"}

    async def first():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sandbox") as c:
            assert (
                await c.post("/admin/fault", json={"scenario": "pool_exhaustion", "idempotency_key": "one"})
            ).status_code == 401
            fault = await c.post(
                "/admin/fault",
                json={"scenario": "pool_exhaustion", "idempotency_key": "one"},
                headers=headers,
            )
            revision = fault.json()["revision"]
            responses = await asyncio.gather(*(c.get("/checkout") for _ in range(36)))
            assert sum(r.status_code == 500 for r in responses) >= 30
            before = (await c.get("/telemetry/metrics", headers=headers)).json()
            assert before["error_rate"] > 0.8
            payload = {
                "idempotency_key": "action-1",
                "expected_revision": revision,
                "target_revision": "stable-v1",
            }
            wrong = await c.post(
                "/admin/rollback", json={**payload, "expected_revision": "wrong"}, headers=headers
            )
            assert wrong.status_code == 409
            response = await c.post("/admin/rollback", json=payload, headers=headers)
            assert response.status_code == 200
            healthy = await asyncio.gather(*(c.get("/checkout") for _ in range(36)))
            assert all(r.status_code == 200 for r in healthy)
            after = (await c.get("/telemetry/metrics", headers=headers)).json()
            assert after["error_rate"] == 0 and after["window_requests"] == 36
            return payload, response.json()

    payload, result = asyncio.run(first())
    restarted = create_sandbox(path, "secret")

    async def second():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://sandbox"
        ) as c:
            replay = await c.post("/admin/rollback", json=payload, headers=headers)
            assert replay.json() == result
            assert (await c.get("/health")).json()["revision"] == "stable-v1"

    asyncio.run(second())


@pytest.mark.parametrize("scenario", ["memory_leak", "dependency_timeout"])
def test_live_non_pool_failures(tmp_path, scenario):
    app = create_sandbox(str(tmp_path / "lab.db"), "secret")
    headers = {"Authorization": "Bearer secret"}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sandbox") as c:
            await c.post(
                "/admin/fault", headers=headers, json={"scenario": scenario, "idempotency_key": "fault"}
            )
            results = await asyncio.gather(*(c.get("/checkout") for _ in range(36)))
            assert any(r.status_code >= 500 for r in results)
            metrics = (await c.get("/telemetry/metrics", headers=headers)).json()
            if scenario == "memory_leak":
                assert metrics["memory_growth_mb"] > 30
            else:
                assert metrics["dependency_latency_ms"] > 300

    asyncio.run(run())
