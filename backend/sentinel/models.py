from datetime import datetime, timezone

from sqlalchemy import JSON, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (UniqueConstraint("tenant", "source_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    source_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(240))
    service: Mapped[str] = mapped_column(String(80), default="checkout")
    connector: Mapped[str] = mapped_column(String(30), default="fixture")
    status: Mapped[str] = mapped_column(String(40), default="investigating", index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    diagnosis: Mapped[dict] = mapped_column(JSON, default=dict)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    recovery: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_state: Mapped[dict] = mapped_column(JSON, default=dict)
    postmortem: Mapped[str] = mapped_column(Text, default="")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    incident_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[float] = mapped_column(Float, default=0)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    lease_owner: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(String(500), default="")


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    incident_id: Mapped[str] = mapped_column(String(40), unique=True)
    plan_hash: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Audit(Base):
    __tablename__ = "audit"
    __table_args__ = (UniqueConstraint("tenant", "sequence"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    incident_id: Mapped[str] = mapped_column(String(40), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[str] = mapped_column(String(40), default=now)
    actor: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    previous_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditHead(Base):
    __tablename__ = "audit_heads"
    tenant: Mapped[str] = mapped_column(String(80), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    hash: Mapped[str] = mapped_column(String(64), default="0" * 64)
