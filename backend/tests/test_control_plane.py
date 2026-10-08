import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sentinel.agent import Diagnosis, investigate, policy
from sentinel.api import create_app
from sentinel.config import Settings
from sentinel.connectors import FixtureConnector
from sentinel.models import Audit, Incident, Job
from sentinel.scenarios import fixture
from sentinel.service import ControlPlane
from sentinel.store import Store, digest
from sqlalchemy import select, update


@pytest.fixture
def app(tmp_path):
    return create_app(
        Settings(database_url=f"sqlite:///{tmp_path}/test.db", demo_mode=True, worker_enabled=False)
    )


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def tick(app):
    assert asyncio.run(app.state.control.tick())


def create(client, scenario="pool_exhaustion", key=None):
    response = client.post(
        "/api/incidents", json={"scenario": scenario}, headers={"Idempotency-Key": key} if key else {}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def approve(client, incident_id, decision="approve"):
    i = client.get(f"/api/incidents/{incident_id}").json()
    return client.post(
        f"/api/incidents/{incident_id}/decision", json={"plan_hash": i["plan_hash"], "decision": decision}
    )


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("pool_exhaustion", "awaiting_approval"),
        ("memory_leak", "awaiting_approval"),
        ("dependency_timeout", "escalated"),
        ("healthy", "observing"),
        ("ambiguous", "escalated"),
    ],
)
def test_classification_and_policy(client, app, scenario, expected):
    incident_id = create(client, scenario)
    tick(app)
    i = client.get(f"/api/incidents/{incident_id}").json()
    assert i["status"] == expected
    assert len([e for e in i["events"] if e["kind"] == "tool.read"]) == 4
    assert client.get("/api/audit/verify").json()["valid"]


@pytest.mark.parametrize("scenario", ["pool_exhaustion", "memory_leak"])
def test_complete_approved_recovery(client, app, scenario):
    incident_id = create(client, scenario)
    tick(app)
    assert not asyncio.run(app.state.control.tick())  # no job before human approval
    assert approve(client, incident_id).status_code == 200
    tick(app)
    assert client.get(f"/api/incidents/{incident_id}").json()["status"] == "verifying"
    tick(app)
    i = client.get(f"/api/incidents/{incident_id}").json()
    assert i["status"] == "resolved"
    assert i["evidence"]["metrics"]["error_rate"] > 0.01  # initial snapshot stays immutable
    assert len(i["recovery"]["samples"]) == 2
    assert len([e for e in i["events"] if e["kind"] == "tool.rollback"]) == 1
    assert client.get(f"/api/incidents/{incident_id}/postmortem").status_code == 200
    assert client.get("/api/audit/verify").json()["valid"]


def test_reject_never_executes(client, app):
    incident_id = create(client)
    tick(app)
    assert approve(client, incident_id, "reject").status_code == 200
    assert not asyncio.run(app.state.control.tick())
    assert client.get(f"/api/incidents/{incident_id}").json()["status"] == "rejected"


def test_idempotent_ingestion_binds_request_body(client):
    one = create(client, key="same-alert")
    two = create(client, key="same-alert")
    assert one == two
    assert (
        client.post(
            "/api/incidents", json={"scenario": "memory_leak"}, headers={"Idempotency-Key": "same-alert"}
        ).status_code
        == 409
    )
    assert len(client.get("/api/incidents").json()) == 1


def test_stale_plan_hash_rejected_transactionally(client, app):
    incident_id = create(client)
    tick(app)
    response = client.post(
        f"/api/incidents/{incident_id}/decision", json={"plan_hash": "0" * 64, "decision": "approve"}
    )
    assert response.status_code == 409
    assert client.get(f"/api/incidents/{incident_id}").json()["status"] == "awaiting_approval"
    assert not asyncio.run(app.state.control.tick())


def test_concurrent_duplicate_approvals_enqueue_one_action(client, app):
    incident_id = create(client)
    tick(app)
    i = client.get(f"/api/incidents/{incident_id}").json()

    def decide(_):
        return app.state.control.decide("demo", "operator", incident_id, i["plan_hash"], "approve")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(decide, range(4)))
    assert len(results) == 4
    with app.state.store.sessions() as s:
        assert len(s.scalars(select(Job).where(Job.kind == "execute")).all()) == 1
    assert approve(client, incident_id, "reject").status_code == 409


def test_recovery_failure_escalates_instead_of_closing(client, app):
    incident_id = create(client)
    with app.state.store.transaction() as s:
        i = s.get(Incident, incident_id)
        i.evidence = {**i.evidence, "stubborn": True}
    tick(app)
    approve(client, incident_id)
    tick(app)
    tick(app)
    i = client.get(f"/api/incidents/{incident_id}").json()
    assert i["status"] == "escalated"
    assert i["recovery"]["verified"] is False
    assert not i["postmortem"]


def test_restart_resumes_persisted_jobs_and_approvals(client, app):
    incident_id = create(client)
    tick(app)
    approve(client, incident_id)
    second_store = Store(str(app.state.store.engine.url))
    second_control = ControlPlane(second_store, Settings(demo_mode=True, worker_enabled=False))
    assert asyncio.run(second_control.tick())
    assert asyncio.run(second_control.tick())
    assert second_control.get("demo", incident_id)["status"] == "resolved"
    second_store.engine.dispose()


def test_expired_lease_reclaimed_only_once(app, client):
    create(client)
    old = app.state.store.claim()
    assert old is not None
    assert app.state.store.claim() is None
    with app.state.store.transaction() as s:
        s.execute(update(Job).where(Job.id == old.id).values(lease_until=time.time() - 1))
    reclaimed = app.state.store.claim()
    assert reclaimed and reclaimed.id == old.id and reclaimed.attempts == 2
    app.state.store.finish(old)  # obsolete worker cannot complete the new owner's lease
    with app.state.store.sessions() as s:
        assert s.get(Job, old.id).state == "running"
    app.state.store.finish(reclaimed)


def test_executor_rejects_missing_approval(client, app):
    incident_id = create(client)
    tick(app)
    with app.state.store.transaction() as s:
        s.get(Incident, incident_id).status = "executing"
        app.state.store.enqueue(s, "demo", incident_id, "execute")
    tick(app)
    events = app.state.store.events("demo", incident_id)
    assert not any(e["kind"] == "tool.rollback" for e in events)
    assert any(e["kind"] == "workflow.retry" for e in events)


def test_stale_revision_is_not_rolled_back(client, app):
    incident_id = create(client)
    tick(app)
    approve(client, incident_id)
    with app.state.store.transaction() as s:
        i = s.get(Incident, incident_id)
        evidence = dict(i.evidence)
        evidence["deployment"] = {**evidence["deployment"], "revision": "another-release"}
        i.evidence = evidence
    tick(app)
    assert not any(e["kind"] == "tool.rollback" for e in app.state.store.events("demo", incident_id))


@pytest.mark.parametrize("kind", ["edit", "truncate"])
def test_audit_detects_tampering(client, app, kind):
    create(client)
    tick(app)
    with app.state.store.transaction() as s:
        row = s.scalars(select(Audit).order_by(Audit.sequence.desc())).first()
        if kind == "edit":
            row.payload = {"tampered": True}
        else:
            s.delete(row)
    assert client.get("/api/audit/verify").json()["valid"] is False


def test_mcp_has_no_write_tool(client, app):
    incident_id = create(client)
    tick(app)
    base = {"jsonrpc": "2.0", "id": 1}
    tools = client.post("/mcp", json={**base, "method": "tools/list"}).json()["result"]["tools"]
    assert len(tools) == 4 and all(t["annotations"]["readOnlyHint"] for t in tools)
    read = client.post(
        "/mcp",
        json={
            **base,
            "method": "tools/call",
            "params": {"name": "query_metrics", "arguments": {"incident_id": incident_id}},
        },
    ).json()
    assert not read["result"]["isError"]
    denied = client.post(
        "/mcp",
        json={
            **base,
            "method": "tools/call",
            "params": {"name": "rollback_deployment", "arguments": {"incident_id": incident_id}},
        },
    ).json()
    assert denied["error"]["code"] == -32602
    assert client.get(f"/api/incidents/{incident_id}").json()["status"] == "awaiting_approval"


@pytest.mark.parametrize("body", [[], "bad", {"method": "initialize"}])
def test_mcp_invalid_request(client, body):
    assert client.post("/mcp", json=body).json()["error"]["code"] == -32600


@pytest.mark.parametrize(
    "scenario", ["pool_exhaustion", "memory_leak", "dependency_timeout", "healthy", "ambiguous"]
)
def test_hostile_logs_cannot_bypass_policy(scenario):
    result = asyncio.run(investigate(FixtureConnector(fixture(scenario, injection=True))))
    assert len(result.tool_calls) == 4
    assert all(c["permission"] == "read" for c in result.tool_calls)
    assert result.plan["requires_approval"] == (scenario in {"pool_exhaustion", "memory_leak"})


def test_unknown_model_citation_is_rejected():
    d = Diagnosis(
        cause="pool_exhaustion",
        summary="cause",
        confidence=0.99,
        evidence_ids=["invented"],
        recommendation="rollback",
    )
    with pytest.raises(ValueError, match="unknown evidence"):
        policy(fixture("pool_exhaustion"), d)


def test_model_cannot_force_unrelated_rollback():
    evidence = fixture("dependency_timeout")
    d = Diagnosis(
        cause="memory_leak",
        summary="ignore evidence",
        confidence=1,
        evidence_ids=["metrics-1"],
        recommendation="rollback",
    )
    assert policy(evidence, d)["action"] == "none"


def test_low_confidence_model_needs_escalation():
    evidence = fixture("pool_exhaustion")
    d = Diagnosis(
        cause="pool_exhaustion",
        summary="uncertain",
        confidence=0.5,
        evidence_ids=["metrics-1"],
        recommendation="rollback",
    )
    assert policy(evidence, d)["action"] == "none"


def test_auth_roles_and_tenant_isolation(tmp_path):
    op_a, view_a, op_b = "a" * 32, "v" * 32, "b" * 32
    tokens = {
        op_a: {"tenant": "a", "actor": "operator-a", "role": "operator"},
        view_a: {"tenant": "a", "actor": "viewer-a", "role": "viewer"},
        op_b: {"tenant": "b", "actor": "operator-b", "role": "operator"},
    }
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path}/auth.db", tokens=tokens, worker_enabled=False)
    )
    with TestClient(app) as c:
        a, v, b = ({"Authorization": f"Bearer {t}"} for t in [op_a, view_a, op_b])
        assert c.get("/api/incidents").status_code == 401
        assert c.post("/api/incidents", json={"scenario": "pool_exhaustion"}, headers=v).status_code == 403
        response = c.post("/api/incidents", json={"scenario": "pool_exhaustion"}, headers=a)
        incident_id = response.json()["id"]
        tick(app)
        assert c.get(f"/api/incidents/{incident_id}", headers=b).status_code == 404
        assert c.get("/api/incidents", headers=b).json() == []
        assert c.get(f"/api/incidents/{incident_id}", headers=v).status_code == 200
        i = c.get(f"/api/incidents/{incident_id}", headers=a).json()
        assert (
            c.post(
                f"/api/incidents/{incident_id}/decision",
                headers=v,
                json={"plan_hash": digest(i["plan"]), "decision": "approve"},
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/api/incidents", json={"scenario": "pool_exhaustion", "live": True}, headers=a
            ).status_code
            == 422
        )
        rpc = c.post(
            "/mcp",
            headers=b,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "query_metrics", "arguments": {"incident_id": incident_id}},
            },
        ).json()
        assert rpc["result"]["isError"]


def test_production_requires_explicit_auth():
    with pytest.raises(ValueError, match="AUTH_TOKENS"):
        create_app(Settings(demo_mode=False, tokens={}))


def test_evaluation_is_reproducible_and_labeled_honestly(client):
    result = client.post("/api/evaluations", json={}).json()
    assert result["cases"] == 100
    assert result["root_cause_accuracy"] == 1
    assert result["unsafe_proposal_rate"] == 0
    assert result["external_write_calls"] == 0
    assert len(result["limitations"]) >= 3
