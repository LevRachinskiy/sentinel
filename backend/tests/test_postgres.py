"""Runs in CI against PostgreSQL; skipped when the service is absent locally."""

import asyncio
import os
import uuid

import pytest
from sentinel.config import Settings
from sentinel.service import ControlPlane
from sentinel.store import Store, digest


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL service not configured")
def test_postgres_durable_workflow():
    url = os.environ["TEST_DATABASE_URL"]
    tenant = "ci-" + uuid.uuid4().hex
    store = Store(url)
    settings = Settings(database_url=url, demo_mode=True, worker_enabled=False)
    control = ControlPlane(store, settings)
    i = control.create(tenant, "ci-operator", "pool_exhaustion", uuid.uuid4().hex)
    asyncio.run(control.tick())
    i = control.get(tenant, i["id"])
    assert i["status"] == "awaiting_approval"
    control.decide(tenant, "ci-operator", i["id"], digest(i["plan"]), "approve")
    store.engine.dispose()
    resumed = Store(url)
    other = ControlPlane(resumed, settings)
    asyncio.run(other.tick())
    asyncio.run(other.tick())
    assert other.get(tenant, i["id"])["status"] == "resolved"
    assert resumed.verify_audit(tenant)["valid"]
    resumed.engine.dispose()
