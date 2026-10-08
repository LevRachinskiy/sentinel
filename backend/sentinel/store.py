import hashlib
import json
import time
import uuid
from contextlib import contextmanager

from sqlalchemy import create_engine, event, or_, select, update
from sqlalchemy.orm import sessionmaker

from .models import Audit, AuditHead, Base, Job, now


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Store:
    def __init__(self, url: str):
        options = (
            {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
        )
        self.engine = create_engine(url, **options)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def pragmas(conn, _):
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=30000")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        with self.sessions.begin() as session:
            yield session

    def ensure_tenant(self, tenant):
        with self.transaction() as s:
            if not s.get(AuditHead, tenant):
                s.add(AuditHead(tenant=tenant))

    def audit(self, s, tenant, incident_id, actor, kind, payload):
        # This UPDATE acquires the per-tenant lock before reading its hash.
        # SQLite serializes writes; PostgreSQL locks only this tenant's head row.
        s.execute(update(AuditHead).where(AuditHead.tenant == tenant).values(sequence=AuditHead.sequence + 1))
        head = s.execute(
            select(AuditHead).where(AuditHead.tenant == tenant).execution_options(populate_existing=True)
        ).scalar_one()
        timestamp = now()
        body = {
            "tenant": tenant,
            "incident_id": incident_id,
            "sequence": head.sequence,
            "timestamp": timestamp,
            "actor": actor,
            "kind": kind,
            "payload": payload,
            "previous_hash": head.hash,
        }
        h = digest(body)
        s.add(Audit(**body, hash=h))
        head.hash = h

    def enqueue(self, s, tenant, incident_id, kind):
        s.add(Job(id=uuid.uuid4().hex, tenant=tenant, incident_id=incident_id, kind=kind))

    def claim(self):
        current = time.time()
        owner = uuid.uuid4().hex
        with self.transaction() as s:
            candidates = s.scalars(
                select(Job)
                .where(
                    Job.available_at <= current,
                    or_(Job.state == "queued", (Job.state == "running") & (Job.lease_until < current)),
                )
                .order_by(Job.available_at, Job.id)
                .limit(10)
            ).all()
            for candidate in candidates:
                result = s.execute(
                    update(Job)
                    .where(
                        Job.id == candidate.id,
                        or_(Job.state == "queued", (Job.state == "running") & (Job.lease_until < current)),
                    )
                    .values(
                        state="running",
                        lease_until=current + 120,
                        lease_owner=owner,
                        attempts=Job.attempts + 1,
                    )
                )
                if result.rowcount:
                    s.expire(candidate)
                    s.refresh(candidate)
                    return candidate
        return None

    def finish(self, job):
        with self.transaction() as s:
            s.execute(
                update(Job).where(Job.id == job.id, Job.lease_owner == job.lease_owner).values(state="done")
            )

    def fail(self, job, error):
        with self.transaction() as s:
            result = s.execute(
                update(Job)
                .where(Job.id == job.id, Job.lease_owner == job.lease_owner)
                .values(
                    state="failed" if job.attempts >= 3 else "queued",
                    error=str(error)[:500],
                    available_at=time.time() + min(2**job.attempts, 30),
                    lease_until=0,
                )
            )
            if result.rowcount:
                self.audit(
                    s,
                    job.tenant,
                    job.incident_id,
                    "worker",
                    "workflow.retry" if job.attempts < 3 else "workflow.failed",
                    {"kind": job.kind, "attempt": job.attempts, "error_type": type(error).__name__},
                )

    def events(self, tenant, incident_id=None, after=0):
        with self.sessions() as s:
            q = select(Audit).where(Audit.tenant == tenant, Audit.sequence > after)
            if incident_id:
                q = q.where(Audit.incident_id == incident_id)
            return [
                {
                    k: getattr(a, k)
                    for k in [
                        "sequence",
                        "timestamp",
                        "actor",
                        "kind",
                        "payload",
                        "incident_id",
                        "previous_hash",
                        "hash",
                        "tenant",
                    ]
                }
                for a in s.scalars(q.order_by(Audit.sequence)).all()
            ]

    def verify_audit(self, tenant):
        previous = "0" * 64
        rows = self.events(tenant)
        for i, row in enumerate(rows, 1):
            body = {k: v for k, v in row.items() if k != "hash"}
            if row["sequence"] != i or row["previous_hash"] != previous or digest(body) != row["hash"]:
                return {"valid": False, "events": len(rows), "broken_at": row["sequence"]}
            previous = row["hash"]
        with self.sessions() as s:
            head = s.get(AuditHead, tenant)
            valid = head is not None and head.sequence == len(rows) and head.hash == previous
        return {"valid": valid, "events": len(rows), "head": previous}
