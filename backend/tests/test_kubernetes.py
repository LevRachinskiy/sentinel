import asyncio
import copy
import json

import pytest
from sentinel.config import Settings
from sentinel.kubernetes import KubernetesConnector
from sentinel.store import digest


@pytest.fixture
def connector(tmp_path):
    template = {
        "metadata": {"labels": {"app": "checkout"}},
        "spec": {
            "containers": [
                {
                    "name": "checkout",
                    "image": "checkout@sha256:stable",
                    "env": [
                        {"name": "DB_POOL_SIZE", "value": "32"},
                        {"name": "RETAIN_BUFFERS", "value": "false"},
                    ],
                }
            ]
        },
    }
    path = tmp_path / "stable.json"
    path.write_text(json.dumps(template))
    settings = Settings(
        kube_template_path=str(path),
        kube_deployment="checkout",
        kube_namespace="lab",
        telemetry_url="http://telemetry",
        kube_tenant="acme",
    )
    connector = KubernetesConnector(settings)
    broken = copy.deepcopy(template)
    broken["spec"]["containers"][0]["env"][0]["value"] = "2"
    deployment = {
        "metadata": {"resourceVersion": "12", "generation": 2},
        "spec": {"template": broken, "replicas": 1},
        "status": {"observedGeneration": 2, "availableReplicas": 1},
    }
    connector.test_deployment = deployment
    connector.test_patches = []

    async def request(method, body=None):
        if method == "PATCH":
            connector.test_patches.append(body)
            assert body[0] == {"op": "test", "path": "/metadata/resourceVersion", "value": "12"}
            assert body[1]["path"] == "/spec/template"
            deployment["spec"]["template"] = body[1]["value"]
            deployment["metadata"]["annotations"] = body[2]["value"]
            deployment["metadata"]["resourceVersion"] = "13"
        return copy.deepcopy(deployment)

    connector.request = request
    return connector


def test_kubernetes_diff_is_captured(connector):
    evidence = asyncio.run(connector.read("get_deployment_diff"))
    assert evidence["revision"] == "12"
    assert evidence["diff"]["pool_size"] == {"before": 32, "after": 2}
    assert evidence["previous_revision"] == digest(connector.template)
    assert connector.path.endswith("/namespaces/lab/deployments/checkout")


def test_kubernetes_rollback_is_idempotent_and_pinned(connector):
    async def run():
        first = await connector.rollback("approval-1", "12", connector.target)
        second = await connector.rollback("approval-1", "12", connector.target)
        assert first["template_digest"] == connector.target
        assert second["already_applied"]
        assert len(connector.test_patches) == 1
        assert (await connector.read("get_deployment_diff"))["template_digest"] == connector.target

    asyncio.run(run())


def test_kubernetes_stale_approval_never_patches(connector):
    with pytest.raises(ValueError, match="stale"):
        asyncio.run(connector.rollback("approval-1", "11", connector.target))
    assert connector.test_patches == []


def test_kubernetes_rejects_unpinned_template(connector):
    with pytest.raises(ValueError, match="operator-pinned"):
        asyncio.run(connector.rollback("approval-1", "12", "arbitrary-target"))
    assert connector.test_patches == []


def test_kubernetes_requires_authenticated_source_configuration():
    with pytest.raises(ValueError, match="Kubernetes requires"):
        Settings(demo_mode=True, kube_tenant="acme").validate()
