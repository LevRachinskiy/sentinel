"""Scoped Kubernetes rollback to an operator-pinned PodTemplate, guarded by JSON Patch tests."""

import json
import ssl
from pathlib import Path
from urllib.parse import quote

import httpx

from .connectors import SandboxConnector
from .models import now
from .store import digest


class KubernetesConnector:
    def __init__(self, settings):
        self.settings = settings
        self.template = json.loads(Path(settings.kube_template_path).read_text())
        if "spec" not in self.template or not self.template["spec"].get("containers"):
            raise ValueError("Stable template must be a Kubernetes PodTemplateSpec")
        self.target = digest(self.template)
        self.path = (
            f"/apis/apps/v1/namespaces/{quote(settings.kube_namespace, safe='')}/deployments/"
            + quote(settings.kube_deployment, safe="")
        )
        self.telemetry = SandboxConnector(settings.telemetry_url, settings.telemetry_token)

    async def request(self, method, body=None):
        # Refresh the mounted projected token each request. CA verification stays enabled.
        token = Path(self.settings.kube_token_path).read_text().strip()
        ca = ssl.create_default_context(cafile=self.settings.kube_ca_path)
        async with httpx.AsyncClient(
            verify=ca, timeout=10, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.request(
                method,
                self.settings.kube_api.rstrip("/") + self.path,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json-patch+json"},
                json=body,
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def env_values(template):
        containers = template["spec"]["containers"]
        # This project investigates one configured checkout deployment/container.
        return {v["name"]: v.get("value") for v in containers[0].get("env", [])}

    async def read(self, tool):
        if tool != "get_deployment_diff":
            return await self.telemetry.read(tool)
        deploy = await self.request("GET")
        metadata = deploy["metadata"]
        before, after = self.env_values(self.template), self.env_values(deploy["spec"]["template"])
        diff = {}
        if before.get("DB_POOL_SIZE") is not None and after.get("DB_POOL_SIZE") is not None:
            diff["pool_size"] = {"before": int(before["DB_POOL_SIZE"]), "after": int(after["DB_POOL_SIZE"])}
        if before.get("RETAIN_BUFFERS") is not None and after.get("RETAIN_BUFFERS") is not None:
            diff["retain_buffers"] = {
                "before": before["RETAIN_BUFFERS"] == "true",
                "after": after["RETAIN_BUFFERS"] == "true",
            }
        status = deploy.get("status", {})
        return {
            "id": "deployment-1",
            "source": "kubernetes",
            "revision": metadata["resourceVersion"],
            "previous_revision": self.target,
            "timestamp": now(),
            "diff": diff,
            "rollback_allowed": True,
            "template_digest": digest(deploy["spec"]["template"]),
            "rollout_healthy": deploy["spec"].get("replicas", 1) > 0
            and status.get("observedGeneration", 0) >= metadata.get("generation", 1)
            and status.get("availableReplicas", 0) == deploy["spec"].get("replicas", 1),
            "namespace": self.settings.kube_namespace,
            "deployment": self.settings.kube_deployment,
        }

    async def rollback(self, key, expected_revision, target_revision):
        if target_revision != self.target:
            raise ValueError("Approved template differs from the operator-pinned stable template")
        deploy = await self.request("GET")
        annotations = deploy["metadata"].get("annotations", {})
        operation = digest(key)
        if (
            annotations.get("sentinel.ops/operation") == operation
            and digest(deploy["spec"]["template"]) == self.target
        ):
            return {"template_digest": self.target, "already_applied": True, "idempotency_key": key}
        if deploy["metadata"]["resourceVersion"] != expected_revision:
            raise ValueError("Kubernetes resourceVersion changed; approval is stale")
        patch = [
            {"op": "test", "path": "/metadata/resourceVersion", "value": expected_revision},
            {"op": "replace", "path": "/spec/template", "value": self.template},
            {
                "op": "add",
                "path": "/metadata/annotations",
                "value": {**annotations, "sentinel.ops/operation": operation},
            },
        ]
        result = await self.request("PATCH", patch)
        return {
            "template_digest": self.target,
            "resource_version": result["metadata"]["resourceVersion"],
            "idempotency_key": key,
        }

    async def sample(self):
        return await self.telemetry.read("query_metrics")
