import asyncio
import contextlib
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .agent import investigate
from .connectors import FixtureConnector, SandboxConnector
from .models import Approval, AuditHead, Incident, Job, now
from .scenarios import SCENARIOS, fixture
from .store import digest


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


def serialize(incident):
    return {
        k: getattr(incident, k)
        for k in [
            "id",
            "title",
            "service",
            "status",
            "created_at",
            "updated_at",
            "connector",
            "evidence",
            "diagnosis",
            "plan",
            "recovery",
            "postmortem",
        ]
    }


class ControlPlane:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def get(self, tenant, incident_id):
        with self.store.sessions() as s:
            i = s.scalar(select(Incident).where(Incident.id == incident_id, Incident.tenant == tenant))
            if not i:
                raise Missing("Incident not found")
            return serialize(i)

    def list(self, tenant):
        with self.store.sessions() as s:
            return [
                serialize(i)
                for i in s.scalars(
                    select(Incident)
                    .where(Incident.tenant == tenant)
                    .order_by(Incident.created_at.desc())
                    .limit(100)
                )
            ]

    def create(self, tenant, actor, scenario, source_key, live=False):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown scenario")
        if live and (not self.settings.demo_mode or tenant != "demo"):
            raise ValueError("Live sandbox injection is restricted to the isolated demo tenant")
        self.store.ensure_tenant(tenant)
        try:
            with self.store.transaction() as s:
                # Serialize creation so two live incidents cannot mutate one shared sandbox.
                s.execute(
                    update(AuditHead).where(AuditHead.tenant == tenant).values(sequence=AuditHead.sequence)
                )
                existing = s.scalar(
                    select(Incident).where(Incident.tenant == tenant, Incident.source_key == source_key)
                )
                if existing:
                    if existing.request_hash != digest({"scenario": scenario, "live": live}):
                        raise Conflict("Idempotency key already belongs to a different request")
                    return serialize(existing)
                if live and s.scalar(
                    select(Incident.id).where(
                        Incident.connector == "sandbox",
                        Incident.status.in_(["investigating", "awaiting_approval", "executing", "verifying"]),
                    )
                ):
                    raise Conflict("Finish the active sandbox incident before injecting another fault")
                i = Incident(
                    id=uuid.uuid4().hex,
                    tenant=tenant,
                    source_key=source_key,
                    request_hash=digest({"scenario": scenario, "live": live}),
                    title=SCENARIOS[scenario]["title"],
                    connector="sandbox" if live else "fixture",
                    evidence={"scenario": scenario} if live else fixture(scenario),
                    status="investigating",
                )
                s.add(i)
                s.flush()
                self.store.audit(
                    s, tenant, i.id, actor, "incident.created", {"scenario": scenario, "live": live}
                )
                self.store.enqueue(s, tenant, i.id, "investigate")
                return serialize(i)
        except IntegrityError:
            with self.store.sessions() as s:
                existing = s.scalar(
                    select(Incident).where(Incident.tenant == tenant, Incident.source_key == source_key)
                )
                if existing:
                    if existing.request_hash != digest({"scenario": scenario, "live": live}):
                        raise Conflict("Idempotency key already belongs to a different request")
                    return serialize(existing)
            raise

    def ingest_alert(self, tenant, actor, title, source_key):
        if tenant != self.settings.kube_tenant:
            raise ValueError("No Kubernetes source is configured for this tenant")
        self.store.ensure_tenant(tenant)
        request_hash = digest({"title": title, "connector": "kubernetes"})
        with self.store.transaction() as s:
            s.execute(update(AuditHead).where(AuditHead.tenant == tenant).values(sequence=AuditHead.sequence))
            existing = s.scalar(
                select(Incident).where(Incident.tenant == tenant, Incident.source_key == source_key)
            )
            if existing:
                if existing.request_hash != request_hash:
                    raise Conflict("Idempotency key belongs to another alert")
                return serialize(existing)
            i = Incident(
                id=uuid.uuid4().hex,
                tenant=tenant,
                source_key=source_key,
                request_hash=request_hash,
                title=title,
                service=self.settings.kube_deployment,
                connector="kubernetes",
                evidence={},
                status="investigating",
            )
            s.add(i)
            s.flush()
            self.store.audit(s, tenant, i.id, actor, "alert.received", {"connector": "kubernetes"})
            self.store.enqueue(s, tenant, i.id, "investigate")
            return serialize(i)

    def decide(self, tenant, actor, incident_id, plan_hash, decision):
        with self.store.transaction() as s:
            changed = s.execute(
                update(Incident)
                .where(
                    Incident.id == incident_id,
                    Incident.tenant == tenant,
                    Incident.status == "awaiting_approval",
                )
                .values(status="executing" if decision == "approve" else "rejected", updated_at=now())
            )
            i = s.scalar(select(Incident).where(Incident.id == incident_id, Incident.tenant == tenant))
            if not i:
                raise Missing("Incident not found")
            if not changed.rowcount:
                prior = s.scalar(
                    select(Approval).where(Approval.incident_id == incident_id, Approval.tenant == tenant)
                )
                if prior and prior.plan_hash == plan_hash and prior.decision == decision:
                    return serialize(i)
                raise Conflict("Incident is not awaiting this decision")
            if not i.plan.get("requires_approval") or digest(i.plan) != plan_hash:
                raise Conflict("Plan changed; refresh the incident before approval")
            s.add(
                Approval(
                    id=uuid.uuid4().hex,
                    tenant=tenant,
                    incident_id=incident_id,
                    actor=actor,
                    plan_hash=plan_hash,
                    decision=decision,
                )
            )
            self.store.audit(s, tenant, incident_id, actor, f"approval.{decision}", {"plan_hash": plan_hash})
            if decision == "approve":
                self.store.enqueue(s, tenant, incident_id, "execute")
            return serialize(i)

    def connector(self, i):
        if i.connector == "kubernetes":
            if i.tenant != self.settings.kube_tenant:
                raise Conflict("Connector tenant mismatch")
            from .kubernetes import KubernetesConnector

            return KubernetesConnector(self.settings)
        if i.connector == "sandbox":
            return SandboxConnector(self.settings.sandbox_url, self.settings.sandbox_token)
        return FixtureConnector(i.execution_state or i.evidence)

    async def process(self, job):
        with self.store.sessions() as s:
            i = s.scalar(
                select(Incident).where(Incident.id == job.incident_id, Incident.tenant == job.tenant)
            )
            if not i:
                raise Missing("Incident not found")
        if job.kind == "investigate":
            if i.status != "investigating":
                return
            connector = self.connector(i)
            if i.connector == "sandbox":
                await connector.inject(i.evidence["scenario"], i.id)
                await connector.traffic()
            result = await investigate(connector, self.settings.provider, self.settings.model)
            if i.connector == "kubernetes":
                result.plan["scope"] = f"{self.settings.kube_namespace}/{self.settings.kube_deployment}"
            with self.store.transaction() as s:
                current = s.get(Incident, i.id)
                if current.status != "investigating":
                    return
                if i.evidence.get("stubborn"):
                    result.evidence["stubborn"] = True
                current.evidence, current.diagnosis, current.plan = (
                    result.evidence,
                    result.diagnosis,
                    result.plan,
                )
                current.status = (
                    "awaiting_approval"
                    if result.plan["requires_approval"]
                    else ("observing" if result.diagnosis["cause"] == "healthy" else "escalated")
                )
                current.updated_at = now()
                for call in result.tool_calls:
                    self.store.audit(s, i.tenant, i.id, "investigator", "tool.read", call)
                self.store.audit(
                    s,
                    i.tenant,
                    i.id,
                    "investigator",
                    "diagnosis.completed",
                    {"diagnosis": result.diagnosis, "usage": result.usage, "plan_hash": digest(result.plan)},
                )
        elif job.kind == "execute":
            if i.status != "executing":
                return
            with self.store.sessions() as s:
                a = s.scalar(
                    select(Approval).where(Approval.incident_id == i.id, Approval.tenant == i.tenant)
                )
                if not a or a.decision != "approve" or a.plan_hash != digest(i.plan):
                    raise Conflict("No matching human approval")
            connector = self.connector(i)
            # Connector performs an atomic revision precondition and persistent idempotency check.
            result = await connector.rollback(
                f"rollback:{i.id}:{digest(i.plan)}", i.plan["expected_revision"], i.plan["target_revision"]
            )
            updated_evidence = dict(i.evidence)
            if i.connector == "fixture":
                updated_evidence = connector.evidence
            with self.store.transaction() as s:
                current = s.get(Incident, i.id)
                if current.status != "executing":
                    return
                current.execution_state = updated_evidence
                current.status, current.updated_at = "verifying", now()
                self.store.audit(s, i.tenant, i.id, "executor", "tool.rollback", result)
                self.store.enqueue(s, i.tenant, i.id, "verify")
        elif job.kind == "verify":
            if i.status != "verifying":
                return
            connector = self.connector(i)
            samples = []
            for _ in range(2):
                samples.append(await connector.sample())
                await asyncio.sleep(0.05)
            deploy = await connector.read("get_deployment_diff")
            healthy = all(
                m.get("window_requests", 0) >= 10
                and m["error_rate"] <= 0.02
                and m["p95_ms"] < 600
                and m.get("memory_growth_mb", 0) < 10
                for m in samples
            )
            revision_matches = (
                (
                    deploy.get("template_digest") == i.plan["target_revision"]
                    and deploy.get("rollout_healthy") is True
                )
                if i.connector == "kubernetes"
                else (deploy["revision"] == i.plan["target_revision"])
            )
            recovered = healthy and revision_matches
            with self.store.transaction() as s:
                current = s.get(Incident, i.id)
                if current.status != "verifying":
                    return
                current.recovery = {
                    "verified": recovered,
                    "samples": samples,
                    "revision": deploy["revision"],
                    "criteria": "2 windows: >=10 requests, <=2% errors, p95<600ms, "
                    "memory growth<10MB, approved revision",
                }
                current.status = "resolved" if recovered else "escalated"
                current.updated_at = now()
                if recovered:
                    current.postmortem = self.postmortem(current)
                self.store.audit(
                    s,
                    i.tenant,
                    i.id,
                    "verifier",
                    "recovery.verified" if recovered else "recovery.failed",
                    current.recovery,
                )
        else:
            raise ValueError("Unknown job kind")

    @staticmethod
    def postmortem(i):
        return (
            f"# {i.title}\n\nIncident: {i.id}\nService: {i.service}\n\n"
            f"## Root cause\n{i.diagnosis['summary']}\n\n"
            f"## Evidence\n"
            + "\n".join(
                f"- {key}: {value['id']}"
                for key, value in i.evidence.items()
                if isinstance(value, dict) and "id" in value
            )
            + f"\n\n## Remediation\nHuman-approved rollback to {i.plan['target_revision']}.\n\n"
            "## Recovery\nTwo recovery samples met the configured thresholds.\n\n"
            "## Follow-up\nAdd a release regression test for this failure and review alert thresholds.\n"
        )

    async def heartbeat(self, job):
        while True:
            await asyncio.sleep(20)
            with self.store.transaction() as s:
                s.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.lease_owner == job.lease_owner)
                    .values(lease_until=time.time() + 120)
                )

    async def tick(self):
        job = self.store.claim()
        if not job:
            return False
        heartbeat = asyncio.create_task(self.heartbeat(job))
        try:
            await self.process(job)
            self.store.finish(job)
        except Exception as exc:
            self.store.fail(job, exc)
            if job.attempts >= 3:
                with self.store.transaction() as s:
                    i = s.get(Incident, job.incident_id)
                    if i and i.tenant == job.tenant:
                        i.status, i.updated_at = "escalated", now()
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        return True
